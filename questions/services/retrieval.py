"""Hybrid retrieval pipeline: semantic + lexical search with RRF fusion.

Pipeline (strategy ``hybrid``, the default)
-------------------------------------------
1. **Candidate scope** - all chunks belonging to documents with status
   ``completed``, restricted to the requested ``document_ids`` when provided.
2. **Semantic channel** - the question is embedded with the multilingual
   model (Persian-capable) and searched against the persistent Chroma index
   (cosine distance). Restricted to the same document scope via a metadata
   filter. Optionally replaced by MMR search for more diverse results.
3. **Lexical channel** - BM25 (``rank_bm25``) over the normalized chunk
   texts. Normalization (Arabic->Persian letter mapping, ZWNJ handling,
   digit folding, case folding) is applied ONLY here, never to stored text.
4. **Fusion** - Reciprocal Rank Fusion combines both ranked lists:

       rrf_score(chunk) = sum over channels of 1 / (RRF_K + rank(channel))

   RRF is rank-based, so it tolerates the incomparable score scales of
   cosine distance and BM25. Chunks found by *both* channels are boosted -
   the strongest signal of genuine relevance.
5. **Post-processing** - de-duplication by chunk id (a chunk appears once
   even if returned by both channels), an optional minimum RRF score
   threshold, and the final ``top_k`` cut.

Strategy ``semantic`` runs the semantic channel alone (kept for
comparisons/experiments; the default is hybrid).

Documented limitations (see docs/TECHNICAL_GUIDE.md):
* BM25 is built per request over the candidate corpus - simple and correct,
  but it re-scans all chunks on every question (fine up to a few thousand
  chunks).
* Semantic search quality depends on the embedding model; Persian works well
  with the default multilingual model but very domain-specific jargon may
  need a fine-tuned model.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from django.conf import settings
from rank_bm25 import BM25Okapi

from core.text import tokenize_for_search
from documents.models import Document, DocumentChunk
from documents.services import vector_store

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetrievedChunk:
    """One chunk returned by the retrieval pipeline."""

    chunk_id: str
    document_id: str
    document_title: str
    chunk_index: int
    content: str
    rrf_score: float
    semantic_rank: int | None = None
    semantic_distance: float | None = None
    lexical_rank: int | None = None


def retrieve_chunks(
    question_text: str,
    document_ids: list[UUID] | None = None,
    top_k: int | None = None,
) -> list[RetrievedChunk]:
    """Retrieve the most relevant chunks for *question_text*.

    Args:
        question_text: the raw user question (original text, not normalized).
        document_ids: optional list of document ids to restrict the search to.
        top_k: override for ``settings.RETRIEVAL_TOP_K``.

    Returns:
        Ranked list of :class:`RetrievedChunk` (best first). Empty list when
        nothing relevant is indexed.
    """
    strategy = settings.RETRIEVAL_STRATEGY
    top_k = top_k or settings.RETRIEVAL_TOP_K
    candidate_k = max(top_k * settings.RETRIEVAL_CANDIDATE_MULTIPLIER, top_k)

    # 1. Candidate scope from PostgreSQL (source of truth).
    chunks_queryset = (
        DocumentChunk.objects.select_related("document")
        .filter(document__status=Document.Status.COMPLETED)
        .order_by("document_id", "chunk_index")
    )
    if document_ids:
        chunks_queryset = chunks_queryset.filter(document_id__in=document_ids)
    chunks = list(chunks_queryset)
    if not chunks:
        logger.info("Retrieval: no completed document chunks in scope.")
        return []

    chunks_by_id = {str(chunk.id): chunk for chunk in chunks}

    # 2. Semantic channel (Chroma, filtered to the same document scope).
    semantic_ranks: dict[str, int] = {}
    semantic_distances: dict[str, float] = {}
    if strategy in {"hybrid", "semantic"}:
        try:
            hits = vector_store.semantic_search(
                question_text,
                k=candidate_k,
                document_ids=document_ids,
                use_mmr=settings.RETRIEVAL_USE_MMR,
            )
        except Exception:
            logger.exception("Semantic search failed")
            raise RuntimeError(
                "Semantic search failed. The embedding provider or vector store "
                "may be unavailable - check EMBEDDINGS_PROVIDER and the "
                "CHROMA_PERSIST_DIRECTORY settings."
            ) from None
        rank = 0
        for metadata, distance in hits:
            chunk_id = metadata.get("chunk_id")
            if chunk_id not in chunks_by_id:
                continue  # stale vector (chunk deleted meanwhile) - ignore
            rank += 1
            semantic_ranks[chunk_id] = rank
            if distance is not None:
                semantic_distances[chunk_id] = distance

    # 3. Lexical channel (BM25 over normalized chunk text).
    lexical_ranks: dict[str, int] = {}
    if strategy == "hybrid":
        query_tokens = tokenize_for_search(question_text)
        corpus_tokens = [tokenize_for_search(chunk.content) for chunk in chunks]
        if query_tokens and any(corpus_tokens):
            try:
                bm25 = BM25Okapi(corpus_tokens)
                scores = bm25.get_scores(query_tokens)
                ranked = sorted(
                    (
                        (float(score), str(chunk.id))
                        for score, chunk in zip(scores, chunks)
                        if score > 0
                    ),
                    key=lambda pair: pair[0],
                    reverse=True,
                )[:candidate_k]
                lexical_ranks = {
                    chunk_id: position
                    for position, (_score, chunk_id) in enumerate(ranked, start=1)
                }
            except ValueError:
                logger.warning(
                    "BM25 could not be initialized for this corpus; "
                    "falling back to the semantic channel only."
                )

    # 4. Reciprocal Rank Fusion.
    rrf_scores: dict[str, float] = {}
    rrf_k = settings.RETRIEVAL_RRF_K
    for chunk_id, rank in semantic_ranks.items():
        rrf_scores[chunk_id] = rrf_scores.get(chunk_id, 0.0) + 1.0 / (rrf_k + rank)
    for chunk_id, rank in lexical_ranks.items():
        rrf_scores[chunk_id] = rrf_scores.get(chunk_id, 0.0) + 1.0 / (rrf_k + rank)
    if not rrf_scores:
        logger.info("Retrieval: neither channel produced results.")
        return []

    ordered = sorted(rrf_scores.items(), key=lambda pair: pair[1], reverse=True)

    # 5. Post-processing: optional threshold, dedupe (implicit), top-k cut.
    min_score = settings.RETRIEVAL_MIN_SCORE
    if min_score > 0:
        ordered = [(cid, score) for cid, score in ordered if score >= min_score]
    ordered = ordered[:top_k]

    results = [
        RetrievedChunk(
            chunk_id=chunk_id,
            document_id=str(chunk.document_id),
            document_title=chunk.document.title,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            rrf_score=round(score, 6),
            semantic_rank=semantic_ranks.get(chunk_id),
            semantic_distance=(
                round(semantic_distances[chunk_id], 6)
                if chunk_id in semantic_distances
                else None
            ),
            lexical_rank=lexical_ranks.get(chunk_id),
        )
        for chunk_id, score in ordered
        if (chunk := chunks_by_id.get(chunk_id)) is not None
    ]
    logger.info(
        "Retrieval: strategy=%s corpus=%d semantic=%d lexical=%d returned=%d",
        strategy,
        len(chunks),
        len(semantic_ranks),
        len(lexical_ranks),
        len(results),
    )
    return results

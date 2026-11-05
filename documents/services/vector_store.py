"""Persistent vector storage backed by ChromaDB and LangChain.

How persistence works
---------------------
A ``chromadb.PersistentClient`` stores the vector index as ordinary files
inside ``settings.CHROMA_PERSIST_DIRECTORY``. In Docker Compose this path is
a named volume (``chroma_data``), so the index survives container
recreation; locally it is the ``chroma_data/`` folder next to ``manage.py``.

Document chunks are stored with stable ids (the chunk UUID) and metadata
(``document_id``, ``chunk_id``, ``chunk_index``). Chunk *titles* are
resolved from PostgreSQL at retrieval time, so renaming a document never
leaves stale metadata in the index.

Concurrency note: the embedded Chroma server writes to a local SQLite file.
For the low write concurrency of this application (uploads happen through
the Admin/API) this is safe; transient ``database is locked`` errors are
retried once. For heavy concurrent write loads, run Chroma in server mode
(see docs/TECHNICAL_GUIDE.md, "Limitations").
"""

from __future__ import annotations

import logging
import time
from uuid import UUID

import chromadb
from django.conf import settings
from langchain_chroma import Chroma

from documents.models import DocumentChunk
from documents.services.embeddings import get_embeddings

logger = logging.getLogger(__name__)

COLLECTION_NAME = "document_chunks"
# Cosine distance makes similarity scores directly interpretable (0 = same
# direction, 2 = opposite) and behaves better than L2 for normalized
# multilingual embeddings.
_COLLECTION_METADATA = {"hnsw:space": "cosine"}

_vector_store: Chroma | None = None


def _get_client() -> chromadb.ClientAPI:
    return chromadb.PersistentClient(
        path=str(settings.CHROMA_PERSIST_DIRECTORY),
        settings=chromadb.Settings(anonymized_telemetry=False),
    )


def get_vector_store() -> Chroma:
    """Return the LangChain ``Chroma`` wrapper for the chunk collection."""
    global _vector_store
    if _vector_store is None:
        _vector_store = Chroma(
            collection_name=COLLECTION_NAME,
            embedding_function=get_embeddings(),
            client=_get_client(),
            collection_metadata=_COLLECTION_METADATA,
        )
    return _vector_store


def reset_cache() -> None:
    """Drop the cached wrapper (used by tests when the persist dir changes)."""
    global _vector_store
    _vector_store = None


def _with_lock_retry(operation):
    """Run *operation*, retrying once after a short pause on SQLite lock errors."""
    try:
        return operation()
    except Exception as exc:
        if "locked" not in str(exc).lower():
            raise
        logger.warning("Chroma reported a locked database; retrying once...")
        time.sleep(0.5)
        return operation()


def _is_stale_handle_error(exc: Exception) -> bool:
    """True when *exc* indicates this process cached a stale collection handle.

    The embedded Chroma server keeps per-process state: vectors written by a
    different process (a management command executed via ``docker compose
    exec``, or another Gunicorn worker) can be invisible to this process's
    cached collection, surfacing as ``InternalError ... Nothing found on
    disk``. A fresh client re-reads the disk state and heals the problem.
    """
    message = str(exc).lower()
    return "nothing found on disk" in message or "hnsw segment reader" in message


def _with_stale_handle_retry(operation):
    """Run *operation*; on a stale-handle error reconnect and retry once."""
    try:
        return operation()
    except Exception as exc:
        if not _is_stale_handle_error(exc):
            raise
        logger.warning(
            "Vector store handle is stale (another process wrote to the "
            "collection); reconnecting and retrying once..."
        )
        reset_cache()
        time.sleep(0.5)
        return operation()


def add_chunks(chunks: list[DocumentChunk]) -> int:
    """Embed and index *chunks*; returns the number of vectors written."""
    if not chunks:
        return 0
    store = get_vector_store()
    texts = [chunk.content for chunk in chunks]
    metadatas = [
        {
            "document_id": str(chunk.document_id),
            "chunk_id": str(chunk.id),
            "chunk_index": chunk.chunk_index,
        }
        for chunk in chunks
    ]
    ids = [str(chunk.id) for chunk in chunks]

    def _add() -> None:
        store.add_texts(texts=texts, metadatas=metadatas, ids=ids)

    _with_stale_handle_retry(lambda: _with_lock_retry(_add))
    logger.info("Indexed %d chunks into the vector store.", len(chunks))
    return len(chunks)


def delete_vectors(chunk_ids: list[UUID]) -> int:
    """Remove the vectors of *chunk_ids* from the collection."""
    if not chunk_ids:
        return 0
    ids = [str(chunk_id) for chunk_id in chunk_ids]

    def _delete() -> None:
        get_vector_store().delete(ids=ids)

    _with_stale_handle_retry(lambda: _with_lock_retry(_delete))
    logger.info("Deleted %d vectors from the vector store.", len(ids))
    return len(ids)


def semantic_search(
    query_text: str,
    k: int,
    document_ids: list[UUID] | None = None,
    use_mmr: bool = False,
) -> list[tuple[dict, float | None]]:
    """Run a semantic search.

    Returns ``(metadata, distance)`` pairs (distance is cosine distance, or
    ``None`` when MMR is used, which does not expose scores). When
    *document_ids* is given, the search is restricted to those documents.
    """
    store = get_vector_store()
    chroma_filter = None
    if document_ids:
        chroma_filter = {"document_id": {"$in": [str(doc_id) for doc_id in document_ids]}}

    if use_mmr:
        documents = _with_stale_handle_retry(
            lambda: store.max_marginal_relevance_search(
                query_text,
                k=k,
                fetch_k=max(k * 4, 10),
                filter=chroma_filter,
            )
        )
        return [(doc.metadata, None) for doc in documents]

    results = _with_stale_handle_retry(
        lambda: store.similarity_search_with_score(query_text, k=k, filter=chroma_filter)
    )
    return [(doc.metadata, float(distance)) for doc, distance in results]


def reset_collection() -> None:
    """Delete the whole collection (used by ``manage.py rebuild_index``)."""
    global _vector_store
    client = _get_client()
    try:
        client.delete_collection(COLLECTION_NAME)
        logger.info("Vector collection '%s' deleted for a full rebuild.", COLLECTION_NAME)
    except Exception:
        # Deleting a non-existing collection is not an error here.
        logger.info("No existing vector collection to delete.")
    _vector_store = None
    # Recreate immediately so stats keep working after a reset.
    get_vector_store()


def get_indexed_chunk_count() -> int:
    """Return the number of vectors in the collection (0 when empty)."""
    client = _get_client()
    # chromadb versions differ: list_collections() returns Collection objects
    # (newer) or plain names (older) - support both.
    existing = [
        collection
        for collection in client.list_collections()
        if getattr(collection, "name", collection) == COLLECTION_NAME
    ]
    if not existing:
        return 0
    return client.get_collection(COLLECTION_NAME).count()

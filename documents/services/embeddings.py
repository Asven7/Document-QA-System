"""Embedding generation with LangChain-compatible interfaces.

Two providers are supported and selected through ``EMBEDDINGS_PROVIDER``:

``fastembed`` (default, production)
    Local ONNX embeddings through the `fastembed <https://github.com/qdrant/fastembed>`_
    library - no API key, no data leaving the machine. The default model is
    ``Qwen/Qwen3-Embedding-0.6B-Q`` (int8-quantized, 1024 dims), a strong
    multilingual model with high-quality Persian support and a 32k-token
    context window. Other supported multilingual models include
    ``intfloat/multilingual-e5-large`` and
    ``sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2``.

``fake``
    A deterministic bag-of-hashed-words embedding used by the automated test
    suite (and usable for offline demos). Texts that share tokens produce
    similar vectors, which makes retrieval behaviour testable without any
    model download.

Why embeddings are needed
-------------------------
An embedding maps a piece of text to a vector so that *semantically similar*
texts land close together in vector space. Retrieval compares the embedding
of a question with the embeddings of all document chunks and returns the
nearest neighbours - this is what makes "annual leave" match «مرخصی
استحقاقی» even across languages. The model is multilingual precisely so
that Persian questions and documents share one vector space.

Query/passage prefixes
----------------------
Some model families (E5, Qwen3, EmbeddingGemma) are trained with task
prefixes; ``_build_prefixers`` applies them transparently:

* E5: ``query: {q}`` / ``passage: {d}``
* Qwen3: ``Instruct: {task}\\nQuery: {q}`` for queries, none for documents
* EmbeddingGemma: ``task: search result | text: {q}`` / ``title: none | text: {d}``
"""

from __future__ import annotations

import hashlib
import logging
import math
import threading
from typing import Callable

from django.conf import settings
from langchain_core.embeddings import Embeddings

logger = logging.getLogger(__name__)

_QWEN3_QUERY_INSTRUCTION = (
    "Given a user question, retrieve relevant document passages that answer "
    "the question"
)


def _build_prefixers(model_name: str) -> tuple[Callable[[str], str], Callable[[str], str]]:
    """Return ``(query_prefixer, passage_prefixer)`` for *model_name*."""
    lowered = model_name.lower()
    if "e5" in lowered:
        return (lambda q: f"query: {q}"), (lambda d: f"passage: {d}")
    if "qwen3" in lowered:
        return (
            lambda q: f"Instruct: {_QWEN3_QUERY_INSTRUCTION}\nQuery: {q}",
            lambda d: d,
        )
    if "embeddinggemma" in lowered:
        return (
            lambda q: f"task: search result | text: {q}",
            lambda d: f"title: none | text: {d}",
        )
    return (lambda q: q), (lambda d: d)


class FastEmbedAdapter(Embeddings):
    """LangChain ``Embeddings`` adapter around ``fastembed.TextEmbedding``.

    The underlying ONNX model is loaded lazily on the first *embedding*
    call and reused for the lifetime of the process. Operations that only
    manage vectors (delete, count) therefore never trigger the (potentially
    large) model download or load.
    """

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._query_prefixer, self._passage_prefixer = _build_prefixers(model_name)
        self._model = None
        # Guards the lazy load below: two threads (e.g. the background
        # processing thread and a request thread) may ask for the model at the
        # same time and must not trigger two concurrent loads/downloads.
        self._load_lock = threading.Lock()

    @property
    def model(self):
        """Return the ``TextEmbedding`` instance, downloading/loading it once."""
        if self._model is None:
            with self._load_lock:
                if self._model is None:
                    from fastembed import TextEmbedding

                    logger.info(
                        "Loading embedding model %s (first use may download it)...", self.model_name
                    )
                    self._model = TextEmbedding(
                        model_name=self.model_name,
                        # Cache the ONNX files under FASTEMBED_CACHE_PATH (a persistent
                        # Docker volume in Compose) so the download happens only once
                        # instead of landing in an ephemeral home directory.
                        cache_dir=str(settings.FASTEMBED_CACHE_PATH),
                    )
        return self._model

    @property
    def is_loaded(self) -> bool:
        """True once the underlying model has been loaded in this process."""
        return self._model is not None

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed document passages (used when indexing chunks)."""
        prefixed = [self._passage_prefixer(text) for text in texts]
        return [[float(value) for value in vector] for vector in self.model.embed(prefixed)]

    def embed_query(self, text: str) -> list[float]:
        """Embed a user question (used at retrieval time)."""
        prefixed = self._query_prefixer(text)
        vector = next(self.model.query_embed([prefixed]))
        return [float(value) for value in vector]


class DeterministicFakeEmbeddings(Embeddings):
    """Offline, deterministic embeddings for tests and demos.

    Every token of the normalized text is hashed into one of ``dim`` buckets
    and the resulting bag-of-words vector is L2-normalized. Two texts that
    share tokens therefore have high cosine similarity - enough to test the
    semantic channel without downloading a model.
    """

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def _embed(self, text: str) -> list[float]:
        from core.text import tokenize_for_search

        vector = [0.0] * self.dim
        tokens = tokenize_for_search(text)
        if not tokens:
            # Keep zero inputs away from the origin so distances stay defined.
            tokens = ["<empty>"]
        for token in tokens:
            digest = hashlib.md5(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dim
            vector[index] += 1.0
            # A second, salted bucket encodes a little word-order signal.
            salted = hashlib.md5(f"{index}:{token}".encode("utf-8")).digest()
            vector[int.from_bytes(salted[:4], "big") % self.dim] += 0.25
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


# ---------------------------------------------------------------------------
# Provider factory with a tiny cache
# ---------------------------------------------------------------------------
_embeddings_cache: dict[tuple[str, str], Embeddings] = {}


def get_embeddings() -> Embeddings:
    """Return the configured LangChain ``Embeddings`` instance (cached)."""
    provider = settings.EMBEDDINGS_PROVIDER
    model = settings.EMBEDDING_MODEL
    cache_key = (provider, model)
    if cache_key not in _embeddings_cache:
        if provider == "fake":
            _embeddings_cache[cache_key] = DeterministicFakeEmbeddings()
        elif provider == "fastembed":
            _embeddings_cache[cache_key] = FastEmbedAdapter(model_name=model)
        else:  # pragma: no cover - guarded by settings validation at startup
            raise ValueError(f"Unknown embeddings provider: {provider!r}")
    return _embeddings_cache[cache_key]


def reset_cache() -> None:
    """Drop cached instances (used by the test suite between settings overrides)."""
    _embeddings_cache.clear()


def warmup_embedding_model() -> int:
    """Load the configured embedding model and run one real embedding.

    Returns the embedding dimension. Used by:

    * the ``embedding-init`` Compose service (via
      ``manage.py preload_embedding_model``) to prove the model is fully
      usable before Django is allowed to start,
    * the Gunicorn ``post_worker_init`` hook (``gunicorn.conf.py``) so every
      worker serves requests only with an operational, already-loaded model.

    Raises whatever the provider raises on failure (missing/invalid cache,
    network problems during download, corrupt files), so failures are loud
    and early instead of surfacing inside the first document upload.
    """
    embeddings = get_embeddings()
    vector = embeddings.embed_query("embedding model warm-up")
    dimension = len(vector)
    logger.info(
        "Embedding model warm-up complete (provider=%s, model=%s, dim=%d).",
        settings.EMBEDDINGS_PROVIDER,
        settings.EMBEDDING_MODEL,
        dimension,
    )
    return dimension

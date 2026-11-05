"""Gunicorn configuration for the Document QA System.

The important hook here is ``post_worker_init``: every Gunicorn worker loads
and verifies the embedding model BEFORE it starts accepting requests, so the
application never reports itself ready while document processing would still
fail (or trigger a download) on first use.

Notes on the warm-up strategy:

* The hook runs inside each worker process (after the app is loaded, before
  the worker serves). Gunicorn's ``--preload`` master-side loading is
  deliberately NOT used: forking a process holding an initialized
  onnxruntime session is unsafe.
* Cost: one model instance per worker (about 1 GB RAM each with the default
  Qwen3 model). ``WEB_WORKERS`` in ``.env`` controls the worker count - use
  1 on memory-constrained machines.
* If the model cannot be loaded (missing/invalid cache, offline mode with an
  incomplete cache, corrupt files), the worker aborts and the container
  fails its healthcheck - a loud, early failure instead of silent breakage.
* Disable with ``EMBEDDING_WARMUP_ON_START=0`` (e.g. for minimal API-only
  images); document processing then loads the model lazily on first use.
"""

from __future__ import annotations

import logging
import os

_TRUTHY = {"1", "true", "yes", "on"}


def _warmup_enabled() -> bool:
    return os.environ.get("EMBEDDING_WARMUP_ON_START", "1").strip().lower() in _TRUTHY


def post_worker_init(worker) -> None:
    """Load and verify the embedding model before this worker serves."""
    if not _warmup_enabled():
        worker.log.info("Embedding warm-up disabled (EMBEDDING_WARMUP_ON_START=0).")
        return
    try:
        from documents.services.embeddings import warmup_embedding_model

        dimension = warmup_embedding_model()
        worker.log.info(
            "Worker %s warm: embedding model loaded and verified (dim=%s).",
            worker.pid,
            dimension,
        )
    except Exception:
        logger = logging.getLogger("documents")
        logger.exception(
            "Embedding model warm-up failed in worker %s - refusing to serve. "
            "Check the embedding-init service logs and the model_cache volume.",
            worker.pid,
        )
        raise

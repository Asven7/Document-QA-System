"""Background document processing.

Processing (chunking + embedding + indexing) runs OUTSIDE the HTTP request:
the view stores the document and hands its pk to a single-worker queue, so
the browser never waits for embedding. This matters most on the very first
start, when the embedding model itself may still be downloading in the
background (see ``manage.py preload_embedding_model``).

Design notes
------------
* One worker per web process keeps writes to the embedded vector store
  serialized and memory predictable - the embedding model is loaded lazily
  inside the processing thread of whichever worker picks up the job.
* The queue lives inside the worker process: jobs interrupted by a restart
  are recovered at container start by ``manage.py reset_stale_processing``.
* Transient SQLite "database table is locked" errors (shared-cache test
  databases / local SQLite runs) are retried a few times; with PostgreSQL
  this never triggers.
* ``DOCUMENT_PROCESSING_ASYNC=False`` (used by the automated test suite)
  keeps processing synchronous inside the request, which makes tests
  deterministic.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor

from django.db import close_old_connections
from django.db.utils import OperationalError

logger = logging.getLogger(__name__)

# A single worker serializes vector-store writes per web process.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="doc-processing")

# Retry budget for transient SQLite lock errors (not used with PostgreSQL).
_MAX_ATTEMPTS = 3
_RETRY_DELAYS_SECONDS = (0.5, 1.0)


def _process_by_pk(document_pk: str) -> None:
    """Load the document in this thread and run the processing pipeline."""
    try:
        close_old_connections()
        from documents.models import Document
        from documents.services.document_service import process_document

        document = Document.objects.get(pk=document_pk)
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                process_document(document)
                return
            except OperationalError as exc:
                # Django re-raises sqlite lock errors as OperationalError.
                if "locked" not in str(exc).lower() or attempt == _MAX_ATTEMPTS:
                    raise
                delay = _RETRY_DELAYS_SECONDS[min(attempt - 1, len(_RETRY_DELAYS_SECONDS) - 1)]
                logger.warning(
                    "Processing of document %s hit a database lock "
                    "(attempt %d/%d); retrying in %.1fs.",
                    document_pk,
                    attempt,
                    _MAX_ATTEMPTS,
                    delay,
                )
                time.sleep(delay)
                close_old_connections()
                document.refresh_from_db()
    except Exception:
        logger.exception("Background processing failed for document %s", document_pk)
    finally:
        close_old_connections()


def queue_processing(document_pk) -> None:
    """Submit *document_pk* to the background processing queue."""
    logger.info("Queued document %s for background processing.", document_pk)
    _executor.submit(_process_by_pk, str(document_pk))


def dispatch_processing(document) -> bool:
    """Process *document* according to ``DOCUMENT_PROCESSING_ASYNC``.

    Only documents in status ``pending`` are dispatched - rows that already
    reached a terminal state (e.g. a DOCX whose extraction failed) are left
    alone. Returns ``True`` when the job was queued for the background
    worker and ``False`` when it was processed synchronously inside the
    caller (or skipped).
    """
    from django.conf import settings
    from documents.models import Document

    if document.status != Document.Status.PENDING:
        return False
    if settings.DOCUMENT_PROCESSING_ASYNC:
        queue_processing(document.pk)
        return True
    from documents.services.document_service import process_document

    process_document(document)
    return False

"""High-level document management service.

A document is text-centric: ``Document.content`` is the single source of
truth that gets chunked and indexed. The service supports two creation
paths and one edit path:

    DOCX upload -> validate -> extract text in memory (binary is discarded)
    Manual text -> validate (non-empty, length cap)
    Edit        -> replace the stored text from the Admin or the API

In every case the lifecycle is:

    content -> checksum (duplicate detection) -> chunk -> embed
            -> index into Chroma -> persist status

The REST API and Django Admin both call these functions, which keeps the
business logic out of views, serializers and admin classes. All processing
happens synchronously inside the request; see docs/TECHNICAL_GUIDE.md for
the reasoning and the Celery upgrade path.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import BinaryIO

from django.core.files.base import File

from documents.exceptions import (
    ContentValidationError,
    DocxValidationError,
    DuplicateDocumentError,
    DocumentProcessingError,
)
from documents.models import Document, DocumentChunk
from documents.services import vector_store
from documents.services.chunking import split_document_text
from documents.services.extraction import DocxExtractionError, extract_docx_text

logger = logging.getLogger(__name__)

_DOCX_MAGIC = b"PK\x03\x04"  # DOCX files are ZIP archives and start with this.


def _zip_contains_word_document(uploaded_file: BinaryIO | File) -> bool:
    """Return True when the ZIP payload contains a Word document part.

    A well-formed ZIP without ``word/document.xml`` is definitely not a
    DOCX file (e.g. a renamed ``.zip``); such uploads are rejected outright.
    A *damaged* ZIP whose central directory cannot be read returns True so
    that the file is still parsed by the extraction service and a ``failed``
    document row with a clear error is stored - the user may have uploaded a
    truncated Word document and the row should stay visible in the Admin.
    """
    import zipfile

    try:
        with zipfile.ZipFile(uploaded_file) as archive:
            names = set(archive.namelist())
    except Exception:
        return True
    finally:
        uploaded_file.seek(0)
    return "word/document.xml" in names


def validate_docx_upload(uploaded_file: BinaryIO | File) -> None:
    """Validate an upload: extension, size, non-emptiness, ZIP container and
    Word document structure.

    Security notes: nothing about the upload is written to disk - the file
    is parsed in memory and only its extracted text is stored, so path
    traversal via crafted filenames is not possible. The upload validation
    only inspects the ZIP container (magic bytes and part names); the deep
    parse of the Word XML happens in the extraction service, which stores a
    ``failed`` status with a readable error instead of raising.
    """
    from django.conf import settings

    name = getattr(uploaded_file, "name", "") or ""
    if not name.lower().endswith(".docx"):
        raise DocxValidationError(
            "Unsupported file type. Only .docx files are accepted."
        )

    max_bytes = settings.UPLOAD_MAX_FILE_SIZE_MB * 1024 * 1024
    size = getattr(uploaded_file, "size", None)
    if size is None:
        uploaded_file.seek(0, 2)
        size = uploaded_file.tell()
        uploaded_file.seek(0)
    if size == 0:
        raise DocxValidationError("The uploaded file is empty.")
    if size > max_bytes:
        raise DocxValidationError(
            f"The file exceeds the maximum upload size of "
            f"{settings.UPLOAD_MAX_FILE_SIZE_MB} MB."
        )

    header = uploaded_file.read(4)
    uploaded_file.seek(0)
    if header != _DOCX_MAGIC:
        raise DocxValidationError(
            "The file does not look like a valid DOCX document "
            "(missing ZIP container signature)."
        )
    if not _zip_contains_word_document(uploaded_file):
        raise DocxValidationError(
            "The file is a ZIP archive but not a DOCX document: it does not "
            "contain a Word document part (word/document.xml)."
        )
    uploaded_file.seek(0)


def compute_content_hash(content: str) -> str:
    """Return the SHA-256 hex digest of the document *content* text."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _validate_text_content(content: str) -> str:
    """Validate manually provided text and return the cleaned value."""
    from django.conf import settings

    cleaned = (content or "").strip()
    if not cleaned:
        raise ContentValidationError("The document text must not be empty.")
    max_chars = settings.DOCUMENT_MAX_TEXT_CHARS
    if len(cleaned) > max_chars:
        raise ContentValidationError(
            f"The document text exceeds the maximum of {max_chars} characters."
        )
    return cleaned


def _default_title_from_text(content: str) -> str:
    first_line = content.strip().splitlines()[0].strip()
    return (first_line[:60] + "...") if len(first_line) > 60 else (first_line or "Untitled document")


def _check_duplicate(content_hash: str, exclude: Document | None = None) -> None:
    queryset = Document.objects.filter(content_hash=content_hash)
    if exclude is not None:
        queryset = queryset.exclude(pk=exclude.pk)
    existing = queryset.first()
    if existing is not None:
        raise DuplicateDocumentError(existing.pk, existing.title)


def process_document(document: Document) -> Document:
    """(Re)process a stored document: chunk its content, embed and index.

    The document row always reflects the outcome: ``completed`` with the
    chunk count, or ``failed`` with a user-readable error message. The
    content itself is never modified or discarded by processing - on failure
    the stale chunks and vectors are removed so the database and the vector
    store stay consistent, and the row can be reprocessed at any time.
    """
    document.status = Document.Status.PROCESSING
    document.error_message = ""
    document.save(update_fields=["status", "error_message", "updated_at"])

    try:
        chunk_texts = split_document_text(document.content)
        if not chunk_texts:
            raise DocxExtractionError(
                "The document text was too short to produce any indexable chunks."
            )
        replace_document_chunks(document, chunk_texts)
    except (DocxExtractionError, DocumentProcessingError) as exc:
        message = (
            exc.message
            if isinstance(exc, DocxExtractionError)
            else str(exc)
        )
        document.status = Document.Status.FAILED
        document.error_message = message
        document.chunk_count = 0
        _discard_chunks_and_vectors(document)
        document.save()
        logger.warning("Document %s failed processing: %s", document.pk, message)
    except Exception:  # unexpected failure - keep the message generic
        document.status = Document.Status.FAILED
        document.error_message = (
            "An unexpected error occurred while processing the document. "
            "Check the server logs for details."
        )
        document.chunk_count = 0
        _discard_chunks_and_vectors(document)
        document.save()
        logger.exception("Unexpected failure while processing document %s", document.pk)
    else:
        document.chunk_count = len(chunk_texts)
        document.status = Document.Status.COMPLETED
        document.save()
        logger.info(
            "Document %s processed: %d characters, %d chunks.",
            document.pk,
            len(document.content),
            len(chunk_texts),
        )
    return document


def _discard_chunks_and_vectors(document: Document) -> None:
    """Remove every chunk row and vector belonging to *document*."""
    chunk_ids = list(document.chunks.values_list("id", flat=True))
    if chunk_ids:
        try:
            vector_store.delete_vectors(chunk_ids)
        except Exception:
            logger.exception(
                "Could not delete vectors of document %s during a failure cleanup.",
                document.pk,
            )
    document.chunks.all().delete()


def replace_document_chunks(document: Document, chunk_texts: list[str]) -> list[DocumentChunk]:
    """Atomically replace the chunks (and vectors) of *document*.

    Old chunks and their vectors are deleted first; then the new chunk rows
    are created and indexed. If indexing fails, the new chunks and any
    partially written vectors are removed again so no half-indexed state
    survives (``rebuild_index`` can always restore from the chunk table).
    """
    _discard_chunks_and_vectors(document)

    chunks = [
        DocumentChunk(document=document, chunk_index=index, content=text)
        for index, text in enumerate(chunk_texts)
    ]
    chunks = list(DocumentChunk.objects.bulk_create(chunks))
    try:
        vector_store.add_chunks(chunks)
    except Exception:
        logger.exception(
            "Embedding/indexing failed for document %s; rolling back %d chunks.",
            document.pk,
            len(chunks),
        )
        try:
            vector_store.delete_vectors([chunk.id for chunk in chunks])
        except Exception:
            logger.exception("Rollback of partial vectors failed for document %s.", document.pk)
        document.chunks.all().delete()
        raise DocumentProcessingError(
            "The document text could not be embedded. The embedding provider "
            "or model may be unavailable; check EMBEDDINGS_PROVIDER / EMBEDDING_MODEL."
        ) from None
    return chunks


def create_document(
    *,
    uploaded_file: File | None = None,
    text: str | None = None,
    title: str | None = None,
    user=None,
    process: bool = True,
) -> Document:
    """Create a document from a DOCX upload *or* manually entered text.

    Exactly one of ``uploaded_file`` / ``text`` must be provided. For a
    DOCX upload the text is extracted in memory; the binary itself is never
    persisted - the database stores the extracted text only.

    A DOCX whose text cannot be extracted (corrupt file) still creates a
    visible row with status ``failed`` and a clear error message.

    When ``process=False`` the row is stored with status ``pending`` and the
    caller is responsible for running :func:`process_document` (typically
    via :func:`documents.services.background.dispatch_processing`, which
    keeps the embedding work out of the HTTP request).

    Raises:
        DocxValidationError: the upload is not an acceptable DOCX file.
        ContentValidationError: the manual text is empty or too long.
        DuplicateDocumentError: identical content already exists.
    """
    if uploaded_file is None and text is None:
        raise ContentValidationError(
            "Provide either a DOCX file or text content to create a document."
        )
    if uploaded_file is not None and text is not None:
        raise ContentValidationError(
            "Provide either a DOCX file or text content, not both."
        )

    if text is not None:
        content = _validate_text_content(text)
        content_hash = compute_content_hash(content)
        _check_duplicate(content_hash)
        document = Document(
            title=(title or "").strip() or _default_title_from_text(content),
            source=Document.Source.TEXT,
            original_filename="",
            content=content,
            content_hash=content_hash,
            uploaded_by=user,
            status=Document.Status.PENDING,
        )
        document.save()
        logger.info(
            "Text document '%s' created by %s (hash=%s...).",
            document.title,
            getattr(user, "username", "anonymous"),
            content_hash[:12],
        )
        return process_document(document) if process else document

    # DOCX upload path
    uploaded_file.seek(0)
    validate_docx_upload(uploaded_file)
    filename = getattr(uploaded_file, "name", "") or "document.docx"
    document = Document(
        title=(title or "").strip() or Path(filename).stem or "Untitled document",
        source=Document.Source.DOCX,
        original_filename=filename,
        content="",
        content_hash=None,
        uploaded_by=user,
        status=Document.Status.PENDING,
    )

    try:
        content = extract_docx_text(uploaded_file)
    except DocxExtractionError as exc:
        document.status = Document.Status.FAILED
        document.error_message = exc.message
        document.save()
        logger.warning(
            "Document '%s' stored with status=failed: %s", document.title, exc.message
        )
        return document

    content = content.strip()
    content_hash = compute_content_hash(content)
    _check_duplicate(content_hash)
    document.content = content
    document.content_hash = content_hash
    document.save()
    logger.info(
        "Document '%s' uploaded by %s (hash=%s...).",
        document.title,
        getattr(user, "username", "anonymous"),
        content_hash[:12],
    )
    return process_document(document) if process else document


def update_document(
    document: Document,
    *,
    title: str | None = None,
    content: str | None = None,
    process: bool = True,
) -> Document:
    """Update the title and/or the text content of *document*.

    Changing the content re-runs the whole processing pipeline; the old
    chunks and vectors are removed as part of it, so search always reflects
    the current text. With ``process=False`` the row is stored with status
    ``pending`` and the caller runs :func:`process_document` (typically via
    :func:`documents.services.background.dispatch_processing`).

    Raises:
        ContentValidationError: the new text is empty or too long.
        DuplicateDocumentError: identical content already exists on another
            document.
    """
    if content is not None:
        cleaned = _validate_text_content(content)
        new_hash = compute_content_hash(cleaned)
        _check_duplicate(new_hash, exclude=document)
        document.content = cleaned
        document.content_hash = new_hash
        document.status = Document.Status.PENDING
        document.error_message = ""
        if title is not None:
            document.title = title.strip()
        document.save()
        return process_document(document) if process else document

    if title is not None and title.strip():
        document.title = title.strip()
        document.save(update_fields=["title", "updated_at"])
    return document


def reprocess_document(document: Document) -> Document:
    """Re-run chunking/indexing for the stored content of *document*."""
    logger.info("Reprocessing document %s.", document.pk)
    return process_document(document)


def delete_document(document: Document) -> None:
    """Delete *document*; chunk vectors are removed by the pre_delete signal."""
    logger.info("Deleting document %s.", document.pk)
    document.delete()

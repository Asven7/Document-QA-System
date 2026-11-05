"""Database models for document management.

Two models are defined:

``Document``
    A document is **text-centric**: its full ``content`` (extracted from an
    uploaded DOCX, or entered manually) is the single source of truth that
    gets chunked and indexed. The binary DOCX file itself is only a transport
    format - it is parsed in memory during upload and never persisted, so the
    database stores the text only. ``source`` records how the text arrived.

``DocumentChunk``
    A persistent piece of the document content produced by the chunking
    service. Chunk rows are the source of truth for the lexical (BM25)
    retrieval channel and for rebuilding the vector index.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.db.models.signals import pre_delete
from django.dispatch import receiver


class Document(models.Model):
    """A text document with its processing state.

    The content can arrive in two ways (``source``):

    * ``docx``  - a DOCX file was uploaded; its full text was extracted in
      memory (the binary is discarded after extraction),
    * ``text``  - the text was entered/pasted directly by the user.

    Editing ``content`` re-runs chunking and re-indexing, so the stored
    chunks and vectors always match the current text.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    class Source(models.TextChoices):
        DOCX = "docx", "DOCX upload"
        TEXT = "text", "Manual text"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255, help_text="Human-readable document title.")
    source = models.CharField(
        max_length=10,
        choices=Source.choices,
        default=Source.DOCX,
        db_index=True,
        help_text="How the document content was provided.",
    )
    original_filename = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="Filename of the uploaded DOCX, when the source is a file upload.",
    )
    content = models.TextField(
        help_text=(
            "Full text content of the document (extracted from the DOCX or "
            "entered manually). This is the document's source of truth."
        ),
    )
    content_hash = models.CharField(
        max_length=64,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            "SHA-256 checksum of the content text, used for duplicate "
            "detection. Null while the content is unknown (processing failed)."
        ),
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
        help_text="Current processing status.",
    )
    error_message = models.TextField(
        blank=True,
        default="",
        help_text="Explanation of the processing failure, when applicable.",
    )
    chunk_count = models.PositiveIntegerField(
        default=0, help_text="Number of chunks generated from this document."
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="documents",
        help_text="User who created the document, when known.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "created_at"])]

    def __str__(self) -> str:
        return f"{self.title} ({self.status})"


class DocumentChunk(models.Model):
    """A persistent chunk of a document's content."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name="chunks",
        help_text="Document this chunk belongs to.",
    )
    chunk_index = models.PositiveIntegerField(
        help_text="Zero-based position of the chunk inside the document."
    )
    content = models.TextField(help_text="Chunk text (original, non-normalized).")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["document_id", "chunk_index"]
        constraints = [
            models.UniqueConstraint(
                fields=["document", "chunk_index"],
                name="unique_chunk_index_per_document",
            )
        ]
        # Note: the unique constraint above already provides a composite index
        # on (document, chunk_index); no separate models.Index is needed.

    def __str__(self) -> str:
        return f"{self.document_id} chunk {self.chunk_index}"


@receiver(pre_delete, sender=Document)
def delete_document_vectors(sender: type[Document], instance: Document, **kwargs) -> None:
    """Remove the document's vectors from Chroma before the row disappears.

    The signal also covers deletions made outside the service layer (Django
    Admin bulk delete, ``QuerySet.delete()`` in a shell, ...), so stale
    vectors can never outlive their document. Chunk rows are deleted by the
    database cascade; vector ids are collected here, while they still exist.
    """
    from documents.services import vector_store

    chunk_ids = list(
        DocumentChunk.objects.filter(document_id=instance.pk).values_list("id", flat=True)
    )
    if chunk_ids:
        try:
            vector_store.delete_vectors(chunk_ids)
        except Exception:
            # Never block the deletion of a document because the vector store
            # is temporarily unavailable; ``rebuild_index`` can clean up later.
            import logging

            logging.getLogger(__name__).exception(
                "Could not delete vectors for document %s; run 'manage.py rebuild_index' "
                "to clean up the vector store.",
                instance.pk,
            )

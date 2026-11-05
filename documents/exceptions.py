"""Domain exceptions raised by the documents services.

The REST layer maps these to HTTP responses; service code stays free of any
HTTP/DRF concerns.
"""

from __future__ import annotations

from uuid import UUID


class DocxValidationError(ValueError):
    """The uploaded file is not an acceptable DOCX document."""


class ContentValidationError(ValueError):
    """Manually provided document text is empty or too long."""


class DuplicateDocumentError(Exception):
    """A document with identical content (same SHA-256) already exists."""

    def __init__(self, existing_id: UUID, existing_title: str) -> None:
        self.existing_id = existing_id
        self.existing_title = existing_title
        super().__init__(
            f"A document with identical content already exists: "
            f"'{existing_title}' (id: {existing_id})."
        )


class DocumentProcessingError(Exception):
    """Document processing failed; the message is safe to show to users."""

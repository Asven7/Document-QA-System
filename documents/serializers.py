"""DRF serializers for document management.

A document can be created in two ways (exactly one must be provided):

* ``file``    - a DOCX upload (multipart/form-data); its text is extracted
  in memory and only the text is stored,
* ``content`` - the document text entered/pasted directly (JSON or form).

Updating accepts the title and/or the text ``content``; replacing the
content re-runs chunking and indexing. Binary file replacement is not
supported (the file itself is never stored).
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from rest_framework import serializers

from documents.exceptions import DocxValidationError
from documents.models import Document
from documents.services.document_service import validate_docx_upload


class DocumentListSerializer(serializers.ModelSerializer):
    """Compact representation used in the paginated list endpoint."""

    class Meta:
        model = Document
        fields = [
            "id",
            "title",
            "source",
            "original_filename",
            "status",
            "chunk_count",
            "error_message",
            "created_at",
            "updated_at",
        ]


class DocumentDetailSerializer(serializers.ModelSerializer):
    """Full representation, including the document text content."""

    uploaded_by = serializers.SerializerMethodField()

    class Meta:
        model = Document
        fields = [
            "id",
            "title",
            "source",
            "original_filename",
            "status",
            "content",
            "content_hash",
            "chunk_count",
            "error_message",
            "uploaded_by",
            "created_at",
            "updated_at",
        ]

    @staticmethod
    def get_uploaded_by(obj: Document) -> str | None:
        return obj.uploaded_by.username if obj.uploaded_by else None


class DocumentCreateSerializer(serializers.Serializer):
    """Input for ``POST /api/v1/documents/`` (file upload or raw text)."""

    file = serializers.FileField(
        required=False,
        allow_null=True,
        help_text=(
            "A .docx file (max %d MB). Its text is extracted and stored; "
            "the binary file itself is not persisted." % settings.UPLOAD_MAX_FILE_SIZE_MB
        ),
    )
    content = serializers.CharField(
        required=False,
        allow_null=True,
        max_length=settings.DOCUMENT_MAX_TEXT_CHARS,
        trim_whitespace=False,
        style={"base_template": "textarea.html"},
        help_text=(
            "The document text, entered/pasted directly (alternative to "
            f"uploading a file; max {settings.DOCUMENT_MAX_TEXT_CHARS} characters)."
        ),
    )
    title = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
        help_text=(
            "Optional title; defaults to the filename (for uploads) or the "
            "first line of the text."
        ),
    )

    def validate_file(self, file) -> object:
        if file is None:
            return None
        try:
            validate_docx_upload(file)
        except DocxValidationError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return file

    def validate_content(self, content) -> str | None:
        if content is None:
            return None
        cleaned = content.strip()
        if not cleaned:
            raise serializers.ValidationError("The document text must not be empty.")
        return cleaned

    def validate(self, attrs: dict) -> dict:
        has_file = attrs.get("file") is not None
        has_content = attrs.get("content") is not None
        if has_file and has_content:
            raise serializers.ValidationError(
                "Provide either a DOCX file or text content, not both."
            )
        if not has_file and not has_content:
            raise serializers.ValidationError(
                "Provide either a DOCX file (multipart) or text content to "
                "create a document."
            )
        if not attrs.get("title", "").strip():
            if has_file:
                attrs["title"] = Path(attrs["file"].name).stem or "Untitled document"
            else:
                first_line = attrs["content"].strip().splitlines()[0].strip()
                attrs["title"] = (
                    (first_line[:60] + "...") if len(first_line) > 60 else first_line
                ) or "Untitled document"
        else:
            attrs["title"] = attrs["title"].strip()
        return attrs


class DocumentUpdateSerializer(serializers.Serializer):
    """Input for ``PATCH /api/v1/documents/{id}/``.

    Both fields are optional; changing ``content`` re-runs chunking and
    indexing so search stays consistent with the edited text.
    """

    title = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
        help_text="New document title.",
    )
    content = serializers.CharField(
        required=False,
        allow_null=True,
        max_length=settings.DOCUMENT_MAX_TEXT_CHARS,
        trim_whitespace=False,
        style={"base_template": "textarea.html"},
        help_text=(
            "New text content for the document. Replaces the stored text and "
            f"re-indexes it (max {settings.DOCUMENT_MAX_TEXT_CHARS} characters)."
        ),
    )

    def validate_content(self, content) -> str | None:
        if content is None:
            return None
        cleaned = content.strip()
        if not cleaned:
            raise serializers.ValidationError("The document text must not be empty.")
        return cleaned

    def validate(self, attrs: dict) -> dict:
        if not attrs.get("title", "").strip():
            attrs.pop("title", None)
        else:
            attrs["title"] = attrs["title"].strip()
        if not attrs:
            raise serializers.ValidationError(
                "Provide at least one of 'title' or 'content' to update."
            )
        return attrs

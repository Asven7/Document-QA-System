"""Admin forms for creating and editing documents.

``DocumentCreateForm`` (add page)
    Offers the two creation paths side by side: upload a DOCX file *or*
    enter/paste the text directly. Exactly one must be provided. Upload
    validation and duplicate detection run in the form, so users get a
    normal red error message and nothing is stored on rejection.

``DocumentChangeForm`` (change page)
    The title and the text content are editable; saving new content
    re-chunks and re-indexes the document. Binary file replacement is not
    offered (the file itself is never stored).
"""

from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError

from documents.exceptions import DocxValidationError
from documents.models import Document
from documents.services.document_service import (
    compute_content_hash,
    validate_docx_upload,
)
from documents.services.extraction import DocxExtractionError, extract_docx_text


def _duplicate_error(existing: Document) -> ValidationError:
    return ValidationError(
        f"A document with identical content already exists: "
        f"'{existing.title}' (id: {existing.pk})."
    )


def _check_duplicate_in_form(content: str, exclude_pk=None) -> None:
    from documents.models import Document as DocumentModel

    queryset = DocumentModel.objects.filter(content_hash=compute_content_hash(content))
    if exclude_pk is not None:
        queryset = queryset.exclude(pk=exclude_pk)
    existing = queryset.first()
    if existing is not None:
        raise _duplicate_error(existing)


class DocumentCreateForm(forms.ModelForm):
    """Add-document form: DOCX upload or manual text (exactly one)."""

    file = forms.FileField(
        required=False,
        label="DOCX file",
        help_text=(
            "Upload a .docx file; its text is extracted and stored "
            "(the binary file itself is not kept)."
        ),
    )
    content = forms.CharField(
        required=False,
        label="Text content",
        widget=forms.Textarea(attrs={"rows": 14}),
        help_text=(
            "Enter or paste the document text directly "
            "(alternative to uploading a file)."
        ),
    )

    class Meta:
        model = Document
        fields = ["title"]

    def clean(self) -> dict:
        cleaned_data = super().clean()
        uploaded_file = cleaned_data.get("file")
        content = cleaned_data.get("content")

        if uploaded_file is None and not (content or "").strip():
            raise ValidationError(
                "Provide a DOCX file or text content - one of the two is required."
            )
        if uploaded_file is not None and (content or "").strip():
            raise ValidationError(
                "Provide either a DOCX file or text content, not both."
            )

        if uploaded_file is not None:
            try:
                validate_docx_upload(uploaded_file)
            except DocxValidationError as exc:
                raise ValidationError({"file": str(exc)}) from exc
            # Duplicate detection needs the extracted text; files that fail
            # extraction are allowed through here and stored as failed rows.
            try:
                extracted = extract_docx_text(uploaded_file).strip()
            except DocxExtractionError:
                return cleaned_data
            if extracted:
                _check_duplicate_in_form(extracted)
        elif content:
            _check_duplicate_in_form(content.strip())
        return cleaned_data


class DocumentChangeForm(forms.ModelForm):
    """Change-document form: editable title and text content."""

    class Meta:
        model = Document
        fields = ["title", "content"]
        widgets = {
            "content": forms.Textarea(attrs={"rows": 16}),
        }

    def clean_content(self) -> str:
        content = (self.cleaned_data.get("content") or "").strip()
        if not content:
            raise ValidationError("The document text must not be empty.")
        return content

    def clean(self) -> dict:
        cleaned_data = super().clean()
        content = cleaned_data.get("content")
        if content and "content" in self.changed_data:
            _check_duplicate_in_form(content, exclude_pk=self.instance.pk)
        return cleaned_data

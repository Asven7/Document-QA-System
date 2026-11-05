"""Django Admin configuration for documents and chunks.

The Admin is the management interface of the application (per the project
specification, no separate frontend is required). It provides:

* create documents by uploading a DOCX file **or** by entering/pasting text,
* edit the text content of an existing document (re-indexes automatically),
* colored processing status, visible error messages and chunk counts,
* a bulk "reprocess selected documents" action,
* a read-only chunk browser with filtering and full-text search.
"""

from __future__ import annotations

import logging

from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest
from django.urls import reverse
from django.utils.html import format_html

from documents.exceptions import DuplicateDocumentError
from documents.forms import DocumentChangeForm, DocumentCreateForm
from documents.models import Document, DocumentChunk
from documents.services import background, document_service

logger = logging.getLogger(__name__)

_STATUS_COLORS = {
    Document.Status.PENDING: "#6c757d",
    Document.Status.PROCESSING: "#0d6efd",
    Document.Status.COMPLETED: "#198754",
    Document.Status.FAILED: "#dc3545",
}


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "source_badge",
        "original_filename",
        "status_badge",
        "chunk_count",
        "chunks_link",
        "uploaded_by",
        "created_at",
        "updated_at",
    )
    list_filter = ("status", "source", "created_at", "uploaded_by")
    search_fields = ("title", "original_filename", "content")
    date_hierarchy = "created_at"
    actions = ("reprocess_selected_documents",)
    list_per_page = 25

    readonly_fields = (
        "id",
        "source",
        "original_filename",
        "status",
        "content_hash",
        "content_preview",
        "error_message",
        "chunk_count",
        "uploaded_by",
        "created_at",
        "updated_at",
    )
    fields = (
        "title",
        "content",
        "id",
        "source",
        "original_filename",
        "status",
        "chunk_count",
        "content_hash",
        "content_preview",
        "error_message",
        "uploaded_by",
        "created_at",
        "updated_at",
    )

    # ------------------------------------------------------------------
    # Form selection: create (file/text) vs change (text editing)
    # ------------------------------------------------------------------
    def get_form(self, request, obj=None, **kwargs):
        kwargs["form"] = DocumentCreateForm if obj is None else DocumentChangeForm
        return super().get_form(request, obj, **kwargs)

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            # Add page: the two creation paths side by side.
            return ((None, {"fields": ("title", "file", "content")}),)
        return super().get_fieldsets(request, obj)

    def changelist_view(self, request: HttpRequest, extra_context=None):
        # Documents are processed asynchronously; while any of them is still
        # pending/processing, the list page refreshes itself every few
        # seconds so status changes are visible without manual reloads.
        extra_context = extra_context or {}
        extra_context["documents_processing"] = Document.objects.filter(
            status__in=[Document.Status.PENDING, Document.Status.PROCESSING]
        ).exists()
        return super().changelist_view(request, extra_context)

    # ------------------------------------------------------------------
    # Display helpers
    # ------------------------------------------------------------------
    @admin.display(description="Source", ordering="source")
    def source_badge(self, obj: Document) -> str:
        labels = {
            Document.Source.DOCX: format_html('<span title="{}">{}</span>', "DOCX upload", "DOCX"),
            Document.Source.TEXT: format_html('<span title="{}">{}</span>', "Manual text", "Text"),
        }
        return labels.get(obj.source, obj.source)

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj: Document) -> str:
        color = _STATUS_COLORS.get(obj.status, "#6c757d")
        return format_html(
            '<span style="color: #fff; background: {}; padding: 2px 10px; '
            'border-radius: 10px; font-size: 11px;">{}</span>',
            color,
            obj.get_status_display(),
        )

    @admin.display(description="Content")
    def content_preview(self, obj: Document) -> str:
        if not obj.content:
            return format_html(
                '<i style="color: #999;">{}</i>', "(no content)"
            )
        escaped = obj.content.replace("<", "&lt;").replace(">", "&gt;")
        return format_html(
            '<pre style="white-space: pre-wrap; max-height: 420px; overflow: auto; '
            'background: #f8f8f8; padding: 8px; border: 1px solid #ddd; '
            'border-radius: 4px; font-size: 12px;">{}</pre>',
            escaped,
        )

    @admin.display(description="Chunks", ordering="chunk_count")
    def chunks_link(self, obj: Document) -> str:
        url = reverse("admin:documents_documentchunk_changelist")
        return format_html(
            '<a href="{}?document__id__exact={}">view</a>', url, obj.pk
        )

    # ------------------------------------------------------------------
    # Save hooks: run the processing service
    # ------------------------------------------------------------------
    def save_model(self, request, obj: Document, form, change: bool) -> None:
        if not change:
            self._create_document(request, form)
            return

        content = form.cleaned_data.get("content") if "content" in form.changed_data else None
        title = form.cleaned_data.get("title")
        if content is not None:
            document = document_service.update_document(
                obj,
                title=title,
                content=content,
                process=False,
            )
            background.dispatch_processing(document)
            self._report_processing(request, document, prefix="was updated")
        else:
            # Metadata-only change (title).
            obj.uploaded_by = obj.uploaded_by or request.user
            super().save_model(request, obj, form, change)

    def _report_processing(self, request: HttpRequest, document: Document, prefix: str) -> None:
        """Show a message reflecting the async/sync processing outcome."""
        if document.status == Document.Status.COMPLETED:
            self.message_user(
                request,
                f"Document '{document.title}' {prefix} and was indexed "
                f"({document.chunk_count} chunks).",
                messages.SUCCESS,
            )
        elif document.status == Document.Status.FAILED:
            self.message_user(
                request,
                f"Document '{document.title}' {prefix}, but processing "
                f"failed: {document.error_message}",
                messages.WARNING,
            )
        else:
            self.message_user(
                request,
                f"Document '{document.title}' {prefix}. Processing continues "
                "in the background (e.g. a first-start embedding-model "
                "download) - refresh the list to see the status.",
                messages.INFO,
            )

    def _create_document(self, request: HttpRequest, form) -> None:
        """Create the document from the add-form (DOCX file or text)."""
        uploaded_file = form.cleaned_data.get("file")
        content = form.cleaned_data.get("content")
        title = form.cleaned_data.get("title")
        try:
            if uploaded_file is not None:
                document = document_service.create_document(
                    uploaded_file=uploaded_file,
                    title=title or None,
                    user=request.user,
                    process=False,
                )
            else:
                document = document_service.create_document(
                    text=content,
                    title=title or None,
                    user=request.user,
                    process=False,
                )
        except DuplicateDocumentError as exc:
            # Backstop; the form validation already reports duplicates.
            self.message_user(request, str(exc), messages.ERROR)
            return
        form.instance.pk = document.pk
        form.instance.id = document.pk
        background.dispatch_processing(document)
        self._report_processing(request, document, prefix="was created")

    # ------------------------------------------------------------------
    # Bulk actions
    # ------------------------------------------------------------------
    @admin.action(description="Reprocess selected documents")
    def reprocess_selected_documents(self, request, queryset: QuerySet[Document]) -> None:
        succeeded = 0
        failed = 0
        for document in queryset:
            document.status = Document.Status.PENDING
            document.error_message = ""
            document.save(update_fields=["status", "error_message", "updated_at"])
            if background.dispatch_processing(document):
                succeeded += 1  # queued; outcome visible in the list
                continue
            result = document_service.reprocess_document(document)
            if result.status == Document.Status.COMPLETED:
                succeeded += 1
            else:
                failed += 1
        if succeeded:
            self.message_user(
                request, f"{succeeded} document(s) reprocessed successfully.", messages.SUCCESS
            )
        if failed:
            self.message_user(
                request,
                f"{failed} document(s) could not be reprocessed (see their error messages).",
                messages.WARNING,
            )


@admin.register(DocumentChunk)
class DocumentChunkAdmin(admin.ModelAdmin):
    """Read-only chunk browser (chunks are generated data)."""

    list_display = ("document_link", "chunk_index", "content_preview", "created_at")
    list_filter = ("document",)
    search_fields = ("content",)
    list_per_page = 25
    list_select_related = ("document",)

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        # Chunks are generated data and cannot be deleted directly. However,
        # deleting them must be allowed *as part of their document's cascade*:
        # Django checks delete permission for every related object it is about
        # to cascade, and returning False here would block deleting the
        # document itself ("Cannot delete document" page). When Django asks
        # about a specific chunk (obj is not None) it is always a cascade;
        # direct bulk deletion from the chunk list (obj is None) stays off.
        if obj is not None:
            return True
        return False

    @admin.display(description="Document", ordering="document__title")
    def document_link(self, obj: DocumentChunk) -> str:
        url = reverse("admin:documents_document_change", args=[obj.document_id])
        return format_html('<a href="{}">{}</a>', url, obj.document.title)

    @admin.display(description="Content")
    def content_preview(self, obj: DocumentChunk) -> str:
        text = obj.content.replace("<", "&lt;").replace(">", "&gt;")
        return format_html(
            '<span title="Open the chunk detail page to see the full content" '
            'style="display:inline-block; max-width: 560px; white-space: nowrap; '
            'overflow: hidden; text-overflow: ellipsis; vertical-align: bottom;">{}</span>',
            text,
        )

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

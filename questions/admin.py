"""Django Admin configuration for the question history.

Besides the standard list/detail screens, the admin provides a dedicated
"Ask a question" page (``/admin/questions/question/ask/``) where a staff
user can submit a question, run the full RAG workflow and inspect the
generated answer and its sources - no separate frontend required.
"""

from __future__ import annotations

import logging

from django.contrib import admin, messages
from django.contrib.admin.utils import quote
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe

from documents.models import Document
from questions.forms import AdminQuestionForm
from questions.models import Question
from questions.services.rag import run_rag_pipeline

logger = logging.getLogger(__name__)

_STATUS_COLORS = {
    Question.Status.PENDING: "#6c757d",
    Question.Status.PROCESSING: "#0d6efd",
    Question.Status.COMPLETED: "#198754",
    Question.Status.FAILED: "#dc3545",
}


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = (
        "short_question",
        "status_badge",
        "model",
        "source_count",
        "asked_by",
        "created_at",
    )
    list_filter = ("status", "created_at", "model")
    search_fields = ("question_text", "answer_text")
    date_hierarchy = "created_at"
    list_per_page = 25
    list_select_related = ("asked_by",)
    readonly_fields = (
        "id",
        "question_text",
        "answer_text",
        "document_scope",
        "sources_table",
        "model",
        "status",
        "error_message",
        "asked_by",
        "created_at",
        "updated_at",
    )
    fields = (
        "id",
        "question_text",
        "status",
        "answer_text",
        "sources_table",
        "document_scope",
        "model",
        "error_message",
        "asked_by",
        "created_at",
        "updated_at",
    )

    # Questions are immutable audit records: view and delete only.
    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj=None) -> bool:
        # The detail page stays readable (change permission enables viewing),
        # but every field is readonly, so nothing can be edited.
        return request.user.is_staff

    # ------------------------------------------------------------------
    # Display helpers
    # ------------------------------------------------------------------
    @admin.display(description="Question")
    def short_question(self, obj: Question) -> str:
        text = obj.question_text[:80]
        url = reverse("admin:questions_question_change", args=[quote(obj.pk)])
        return format_html('<a href="{}">{}</a>', url, text)

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj: Question) -> str:
        color = _STATUS_COLORS.get(obj.status, "#6c757d")
        return format_html(
            '<span style="color: #fff; background: {}; padding: 2px 10px; '
            'border-radius: 10px; font-size: 11px;">{}</span>',
            color,
            obj.get_status_display(),
        )

    @admin.display(description="Sources")
    def source_count(self, obj: Question) -> int:
        return len(obj.sources or [])

    @admin.display(description="Retrieved sources")
    def sources_table(self, obj: Question) -> str:
        sources = obj.sources or []
        if not sources:
            return format_html(
                '<i style="color: #999;">{}</i>', "(no sources retrieved)"
            )
        rows = format_html_join(
            "\n",
            "<tr>"
            '<td style="padding: 4px 8px;">[Source {number}]</td>'
            '<td style="padding: 4px 8px;"><a href="{url}">{title}</a></td>'
            '<td style="padding: 4px 8px;">{doc_id}</td>'
            '<td style="padding: 4px 8px;">{chunk_index}</td>'
            '<td style="padding: 4px 8px;">{score}</td>'
            '<td style="padding: 4px 8px;">sem. rank {sem}, lex. rank {lex}</td>'
            "</tr>",
            (
                {
                    "number": position,
                    "url": reverse("admin:documents_document_change", args=[source["document_id"]]),
                    "title": source.get("document_title", "?"),
                    "doc_id": str(source.get("document_id", "?"))[:13] + "...",
                    "chunk_index": source.get("chunk_index", "?"),
                    "score": source.get("rrf_score", "?"),
                    "sem": source.get("semantic_rank") or "-",
                    "lex": source.get("lexical_rank") or "-",
                }
                for position, source in enumerate(sources, start=1)
            ),
        )
        header = mark_safe(
            "<tr>"
            "<th style='padding: 4px 8px; text-align: left;'>Label</th>"
            "<th style='padding: 4px 8px; text-align: left;'>Document</th>"
            "<th style='padding: 4px 8px; text-align: left;'>Document ID</th>"
            "<th style='padding: 4px 8px; text-align: left;'>Chunk</th>"
            "<th style='padding: 4px 8px; text-align: left;'>RRF score</th>"
            "<th style='padding: 4px 8px; text-align: left;'>Channel ranks</th>"
            "</tr>"
        )
        return format_html(
            '<table style="border-collapse: collapse; border: 1px solid #ddd;">'
            "{}{}</table>",
            header,
            rows,
        )

    # ------------------------------------------------------------------
    # Custom admin page: ask a question and inspect the result
    # ------------------------------------------------------------------
    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "ask/",
                self.admin_site.admin_view(self.ask_question_view),
                name="questions_question_ask",
            ),
        ]
        return custom_urls + urls

    def ask_question_view(self, request: HttpRequest) -> HttpResponse:
        """Render the question form and run the RAG pipeline on submit."""
        if request.method == "POST":
            form = AdminQuestionForm(request.POST)
            if form.is_valid():
                document_ids = list(
                    form.cleaned_data["document_scope"].values_list("id", flat=True)
                )
                question = run_rag_pipeline(
                    question_text=form.cleaned_data["question_text"],
                    document_ids=document_ids or None,
                    user=request.user,
                )
                if question.status == Question.Status.COMPLETED:
                    messages.success(
                        request,
                        f"Question answered successfully ({len(question.sources)} source(s) used).",
                    )
                else:
                    messages.error(
                        request,
                        f"The question could not be answered: {question.error_message}",
                    )
                return redirect("admin:questions_question_change", question.pk)
        else:
            form = AdminQuestionForm()

        context = {
            **self.admin_site.each_context(request),
            "title": "Ask a question",
            "form": form,
            "processed_documents": Document.objects.filter(
                status=Document.Status.COMPLETED
            ).count(),
        }
        return render(request, "admin/questions/ask_question.html", context)

    def changelist_view(self, request: HttpRequest, extra_context=None) -> HttpResponse:
        extra_context = extra_context or {}
        extra_context["ask_url"] = reverse("admin:questions_question_ask")
        return super().changelist_view(request, extra_context=extra_context)

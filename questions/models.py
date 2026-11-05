"""Database model for the question and answer history."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models


class Question(models.Model):
    """A submitted question together with its generated answer and sources.

    Records are immutable from the API's point of view (history integrity);
    Django Admin allows deletion for cleanup but not editing.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    question_text = models.TextField(help_text="Question submitted by the user.")
    answer_text = models.TextField(
        blank=True,
        default="",
        help_text="Generated answer (empty while processing or on failure).",
    )
    document_scope = models.ManyToManyField(
        "documents.Document",
        blank=True,
        related_name="questions",
        help_text="Documents the question was restricted to; empty means all documents.",
    )
    sources = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "Retrieved chunks used as context: list of objects with "
            "document_id, document_title, chunk_id, chunk_index and ranking "
            "scores. No API keys or secrets are ever stored here."
        ),
    )
    model = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="LLM model identifier used to generate the answer.",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    error_message = models.TextField(
        blank=True,
        default="",
        help_text="Human-readable failure reason, when applicable.",
    )
    asked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="questions",
        help_text="User who submitted the question, when known.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "created_at"])]

    def __str__(self) -> str:
        return f"{self.question_text[:60]} ({self.status})"

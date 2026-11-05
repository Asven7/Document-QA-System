"""Admin form for submitting a question directly from Django Admin."""

from __future__ import annotations

from django import forms

from documents.models import Document


class AdminQuestionForm(forms.Form):
    """Form used by the "Ask a question" admin page."""

    question_text = forms.CharField(
        label="Question",
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 4, "style": "width: 100%; direction: auto;"}),
        help_text="Ask in any language - English and Persian are fully supported.",
    )
    document_scope = forms.ModelMultipleChoiceField(
        label="Restrict to documents (optional)",
        queryset=Document.objects.filter(status=Document.Status.COMPLETED).order_by("title"),
        required=False,
        widget=forms.SelectMultiple(attrs={"style": "min-width: 320px; height: 120px;"}),
        help_text="Leave empty to search all processed documents.",
    )

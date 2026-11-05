"""DRF serializers for question submission and history."""

from __future__ import annotations

from rest_framework import serializers

from documents.models import Document
from questions.models import Question


class QuestionCreateSerializer(serializers.Serializer):
    """Input for ``POST /api/v1/questions/``.

    Persian question text is fully supported (UTF-8 end to end).
    """

    question_text = serializers.CharField(
        min_length=3,
        max_length=2000,
        help_text="The question, in any language (English and Persian are tested).",
    )
    document_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        allow_null=True,
        help_text=(
            "Optional list of document ids restricting the retrieval. "
            "Omit or pass null to search all processed documents."
        ),
    )

    def validate_document_ids(self, value: list | None):
        if value is None:
            return None
        if not value:
            raise serializers.ValidationError(
                "Provide at least one document id, or omit 'document_ids' to "
                "search all documents."
            )
        unique_ids = list(dict.fromkeys(str(item) for item in value))
        found = set(
            Document.objects.filter(id__in=unique_ids).values_list("id", flat=True)
        )
        found_str = {str(item) for item in found}
        missing = [item for item in unique_ids if item not in found_str]
        if missing:
            raise serializers.ValidationError(
                f"Unknown document id(s): {', '.join(missing)}."
            )
        completed = set(
            Document.objects.filter(
                id__in=unique_ids, status=Document.Status.COMPLETED
            ).values_list("id", flat=True)
        )
        if not completed:
            raise serializers.ValidationError(
                "None of the selected documents has been processed successfully, "
                "so nothing could be searched. Check document statuses first."
            )
        return list(found)


class QuestionSerializer(serializers.ModelSerializer):
    """Full representation of a stored question (history record)."""

    document_scope = serializers.SerializerMethodField()
    asked_by = serializers.SerializerMethodField()

    class Meta:
        model = Question
        fields = [
            "id",
            "question_text",
            "answer_text",
            "status",
            "error_message",
            "sources",
            "model",
            "document_scope",
            "asked_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    @staticmethod
    def get_document_scope(obj: Question) -> list[str]:
        return [str(document_id) for document_id in obj.document_scope.values_list("id", flat=True)]

    @staticmethod
    def get_asked_by(obj: Question) -> str | None:
        return obj.asked_by.username if obj.asked_by else None

"""REST API views for questions."""

from __future__ import annotations

import logging

from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.response import Response

from questions.models import Question
from questions.serializers import QuestionCreateSerializer, QuestionSerializer
from questions.services.rag import run_rag_pipeline

logger = logging.getLogger(__name__)


class QuestionViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Submit questions and browse the answer history.

    Endpoints (all require authentication):

    * ``POST /api/v1/questions/``     submit a question (synchronous RAG)
    * ``GET  /api/v1/questions/``     paginated history (``?status=``, ``?search=``)
    * ``GET  /api/v1/questions/{id}/`` single record with answer and sources

    Question records are immutable: updating or deleting history is not
    supported on the API by design (audit integrity). Django Admin can be
    used for cleanup.
    """

    queryset = (
        Question.objects.select_related("asked_by")
        .prefetch_related("document_scope")
        .order_by("-created_at")
    )

    def get_serializer_class(self) -> type:
        if self.action == "create":
            return QuestionCreateSerializer
        return QuestionSerializer

    @extend_schema(
        summary="Submit a question",
        description=(
            "Submits a question and runs the full RAG pipeline synchronously: "
            "hybrid retrieval (semantic + BM25 with Reciprocal Rank Fusion), "
            "prompt construction and answer generation via the configured "
            "OpenRouter model. The response contains the answer, the retrieved "
            "sources and the model identifier. Persian questions are answered "
            "in Persian. No LLM call is made when nothing relevant is indexed - "
            "in that case a localized 'insufficient context' answer is returned."
        ),
        responses={201: QuestionSerializer, 400: dict},
    )
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        question = run_rag_pipeline(
            question_text=serializer.validated_data["question_text"],
            document_ids=serializer.validated_data.get("document_ids"),
            user=request.user if request.user.is_authenticated else None,
        )
        response_serializer = QuestionSerializer(question, context=self.get_serializer_context())
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(
        summary="Question history",
        description=(
            "Paginated list of previously asked questions with answers, "
            "sources and statuses. Filter with ``?status=completed|failed`` "
            "and search with ``?search=<text>``."
        ),
    )
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        status_filter = request.query_params.get("status")
        if status_filter:
            valid_statuses = {choice[0] for choice in Question.Status.choices}
            if status_filter not in valid_statuses:
                return Response(
                    {"status": [f"Invalid status filter. Valid values: {sorted(valid_statuses)}."]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            queryset = queryset.filter(status=status_filter)
        search_term = (request.query_params.get("search") or "").strip()
        if search_term:
            queryset = queryset.filter(question_text__icontains=search_term)
        page = self.paginate_queryset(queryset)
        serializer = self.get_serializer(page if page is not None else queryset, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

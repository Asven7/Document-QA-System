"""REST API views for document management."""

from __future__ import annotations

import logging

from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from documents.exceptions import (
    ContentValidationError,
    DocxValidationError,
    DuplicateDocumentError,
)
from documents.models import Document
from documents.serializers import (
    DocumentCreateSerializer,
    DocumentDetailSerializer,
    DocumentListSerializer,
    DocumentUpdateSerializer,
)
from documents.services import background, document_service

logger = logging.getLogger(__name__)


class DocumentViewSet(viewsets.ModelViewSet):
    """CRUD + reprocess endpoints for documents.

    A document is text-centric: it is created either from a DOCX upload
    (whose text is extracted and stored - the binary is not persisted) or
    from directly entered text, and its text can be edited later.
    Processing (chunking + embedding + indexing) runs in the background by
    default, so responses return immediately with status ``pending``;
    with ``DOCUMENT_PROCESSING_ASYNC=False`` processing is synchronous.

    Endpoints (all require authentication; see README section 15):

    * ``GET    /api/v1/documents/``            paginated list (``?status=``, ``?page=``, ``?page_size=``)
    * ``POST   /api/v1/documents/``            create from a DOCX upload (multipart) or from text (JSON)
    * ``GET    /api/v1/documents/{id}/``       detail incl. the stored text
    * ``PATCH  /api/v1/documents/{id}/``       update title and/or text content (re-indexes)
    * ``DELETE /api/v1/documents/{id}/``       delete document, chunks and vectors
    * ``POST   /api/v1/documents/{id}/reprocess/``  queue re-chunking / re-indexing (202)
    """

    queryset = Document.objects.all().order_by("-created_at")
    lookup_field = "pk"

    def get_serializer_class(self) -> type:
        if self.action == "create":
            return DocumentCreateSerializer
        if self.action in {"update", "partial_update"}:
            return DocumentUpdateSerializer
        if self.action == "list":
            return DocumentListSerializer
        return DocumentDetailSerializer

    # ------------------------------------------------------------------
    # Standard CRUD, routed through the service layer
    # ------------------------------------------------------------------
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            document = document_service.create_document(
                uploaded_file=serializer.validated_data.get("file"),
                text=serializer.validated_data.get("content"),
                title=serializer.validated_data["title"],
                user=request.user if request.user.is_authenticated else None,
                # Keep embedding (and a possible first-start model download)
                # out of the HTTP request; the service layer decides between
                # the background queue and synchronous processing.
                process=False,
            )
        except (DocxValidationError, ContentValidationError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except DuplicateDocumentError as exc:
            return Response(
                {
                    "detail": str(exc),
                    "existing_document_id": str(exc.existing_id),
                },
                status=status.HTTP_409_CONFLICT,
            )
        background.dispatch_processing(document)
        response_serializer = DocumentDetailSerializer(document, context=self.get_serializer_context())
        headers = self.get_success_headers(response_serializer.data)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        try:
            document = document_service.update_document(
                instance,
                title=serializer.validated_data.get("title"),
                content=serializer.validated_data.get("content"),
                process=False,
            )
        except (DocxValidationError, ContentValidationError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except DuplicateDocumentError as exc:
            return Response(
                {
                    "detail": str(exc),
                    "existing_document_id": str(exc.existing_id),
                },
                status=status.HTTP_409_CONFLICT,
            )
        background.dispatch_processing(document)
        response_serializer = DocumentDetailSerializer(document, context=self.get_serializer_context())
        return Response(response_serializer.data)

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        document = self.get_object()
        document_service.delete_document(document)
        return Response(status=status.HTTP_204_NO_CONTENT)

    # ------------------------------------------------------------------
    # Filtering for the list endpoint (?status=completed|failed|...)
    # ------------------------------------------------------------------
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        status_filter = request.query_params.get("status")
        if status_filter:
            valid_statuses = {choice[0] for choice in Document.Status.choices}
            if status_filter not in valid_statuses:
                return Response(
                    {"status": [f"Invalid status filter. Valid values: {sorted(valid_statuses)}."]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            queryset = queryset.filter(status=status_filter)
        page = self.paginate_queryset(queryset)
        serializer = self.get_serializer(page if page is not None else queryset, many=True)
        if page is not None:
            return self.get_paginated_response(serializer.data)
        return Response(serializer.data)

    # ------------------------------------------------------------------
    # Custom actions
    # ------------------------------------------------------------------
    @extend_schema(
        operation_id="documents_reprocess",
        summary="Reprocess a document",
        description=(
            "Re-chunks and re-indexes the stored text. Useful after changing "
            "CHUNK_SIZE/CHUNK_OVERLAP/EMBEDDING_MODEL or after a transient "
            "processing failure. Processing runs in the background; the "
            "response returns immediately with status 'pending'."
        ),
        request=None,
        responses={202: DocumentDetailSerializer, 200: DocumentDetailSerializer},
    )
    @action(detail=True, methods=["post"])
    def reprocess(self, request, pk=None):
        document = self.get_object()
        document.status = Document.Status.PENDING
        document.error_message = ""
        document.save(update_fields=["status", "error_message", "updated_at"])
        queued = background.dispatch_processing(document)
        response = DocumentDetailSerializer(document, context=self.get_serializer_context())
        return Response(
            response.data,
            status=status.HTTP_202_ACCEPTED if queued else status.HTTP_200_OK,
        )

"""System-level API views: health check and token issuance."""

from __future__ import annotations

import logging

from django.db import connection
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from documents.services import vector_store
from questions.services.llm import get_llm_model_name, is_llm_configured

logger = logging.getLogger(__name__)


def _embedding_status() -> dict:
    """Embedding-model readiness information (no model load is triggered).

    ``loaded_in_process`` is True only after this Gunicorn worker actually
    loaded the model (warm-up or first use); the ``embedding-init`` Compose
    service is what guarantees the on-disk cache is complete before the web
    container even starts.
    """
    import os

    from django.conf import settings

    loaded = False
    try:
        from documents.services.embeddings import get_embeddings

        adapter = get_embeddings()
        loaded = bool(getattr(adapter, "is_loaded", False))
        model_name = getattr(adapter, "model_name", settings.EMBEDDING_MODEL)
    except Exception:
        model_name = settings.EMBEDDING_MODEL
    return {
        "provider": settings.EMBEDDINGS_PROVIDER,
        "model": model_name,
        "cache_path": str(settings.FASTEMBED_CACHE_PATH),
        "offline_mode": os.environ.get("HF_HUB_OFFLINE", "0").strip().lower()
        in {"1", "true", "yes", "on"},
        "loaded_in_process": loaded,
    }


class HealthView(APIView):
    """Public liveness/health endpoint.

    Reports the state of the database and the vector store, plus whether the
    LLM is configured. A missing LLM key does **not** make the service
    unhealthy: the application is intentionally usable for document
    management and admin setup until an OpenRouter key is provided.
    """

    authentication_classes: list = []
    permission_classes = [AllowAny]

    @extend_schema(
        operation_id="health_check",
        summary="Service health check",
        description=(
            "Returns database and vector store status, embedding-model "
            "readiness and whether the LLM is configured. No authentication "
            "required."
        ),
        responses={200: dict, 503: dict},
    )
    def get(self, request: Request) -> Response:
        database_ok = True
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
        except Exception:
            logger.exception("Health check: database query failed")
            database_ok = False

        vector_ok = True
        indexed_chunks = None
        try:
            indexed_chunks = vector_store.get_indexed_chunk_count()
        except Exception:
            logger.exception("Health check: vector store inspection failed")
            vector_ok = False

        payload = {
            "status": "ok" if (database_ok and vector_ok) else "error",
            "database": {"status": "ok" if database_ok else "error"},
            "vector_store": {
                "status": "ok" if vector_ok else "error",
                "indexed_chunks": indexed_chunks,
            },
            "embedding": _embedding_status(),
            "llm": {
                "configured": is_llm_configured(),
                "model": get_llm_model_name(),
            },
        }
        http_status = (
            status.HTTP_200_OK
            if (database_ok and vector_ok)
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        return Response(payload, status=http_status)


class TokenObtainView(ObtainAuthToken):
    """Issue a DRF token for API clients using username + password.

    The returned token is passed by clients in the ``Authorization: Token ...``
    header. Tokens can also be created and revoked from Django Admin
    (Home -> Auth Token -> Tokens).
    """

    @extend_schema(
        operation_id="obtain_api_token",
        summary="Obtain an API token",
        description=(
            "Exchange a username and password for a token to use with the "
            "REST API. Use a Django staff account. Send the returned token in "
            "the ``Authorization: Token <token>`` header of every API request."
        ),
        responses={
            200: OpenApiResponse(
                description="Returns {token, user_id, username}.",
                response={
                    "type": "object",
                    "properties": {
                        "token": {"type": "string"},
                        "user_id": {"type": "integer"},
                        "username": {"type": "string"},
                    },
                },
            ),
            400: OpenApiResponse(description="Invalid credentials."),
        },
    )
    def post(self, request: Request, *args, **kwargs) -> Response:
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        token, _created = Token.objects.get_or_create(user=user)
        return Response(
            {
                "token": token.key,
                "user_id": user.pk,
                "username": user.username,
            }
        )

"""Tests for the public health endpoint and LLM configuration helpers."""

from __future__ import annotations

import shutil
import tempfile
from unittest.mock import patch

from django.test import override_settings
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from questions.services.llm import (
    get_llm_model_name,
    is_llm_configured,
    translate_llm_exception,
)


class HealthEndpointTests(APITestCase):
    def setUp(self) -> None:
        self.client = APIClient()
        # Keep the health tests hermetic: point the vector store at a temp dir.
        self._tmpdir = tempfile.mkdtemp(prefix="health_chroma_")
        override = override_settings(CHROMA_PERSIST_DIRECTORY=self._tmpdir)
        override.enable()
        self.addCleanup(override.disable)
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        from documents.services import embeddings as emb, vector_store as vs

        emb.reset_cache()
        vs.reset_cache()
        self.addCleanup(emb.reset_cache)
        self.addCleanup(vs.reset_cache)

    def test_health_is_public(self):
        response = self.client.get("/api/health/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "ok")
        self.assertEqual(response.data["database"]["status"], "ok")
        self.assertEqual(response.data["vector_store"]["status"], "ok")
        self.assertIn("indexed_chunks", response.data["vector_store"])
        self.assertIn("configured", response.data["llm"])

    def test_health_reports_database_failure(self):
        with patch(
            "django.db.connection.cursor",
            side_effect=RuntimeError("database down"),
        ):
            response = self.client.get("/api/health/")
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data["status"], "error")
        self.assertEqual(response.data["database"]["status"], "error")

    def test_health_reports_vector_store_state_without_llm_key(self):
        response = self.client.get("/api/health/")
        self.assertEqual(response.data["llm"]["configured"], False)
        # The service stays healthy without an LLM key by design.
        self.assertEqual(response.data["status"], "ok")


class LLMConfigurationTests(APITestCase):
    def test_is_llm_configured_reflects_settings(self):
        self.assertFalse(is_llm_configured())
        with override_settings(OPENROUTER_API_KEY="some-key"):
            self.assertTrue(is_llm_configured())

    def test_model_name_reported(self):
        with override_settings(OPENROUTER_MODEL="test/model"):
            self.assertEqual(get_llm_model_name(), "test/model")

    def test_get_chat_model_without_key_raises_helpful_error(self):
        from questions.services.llm import LLMNotConfiguredError, get_chat_model

        with self.assertRaises(LLMNotConfiguredError) as ctx:
            get_chat_model()
        self.assertIn("OPENROUTER_API_KEY", str(ctx.exception))

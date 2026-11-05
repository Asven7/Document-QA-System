"""Tests for background document processing and the recovery commands.

The production default runs chunking+embedding in a background worker
thread (``DOCUMENT_PROCESSING_ASYNC=True``) so requests never wait for
embedding - which may include a one-time embedding-model download on the
first container start. These tests exercise that async path end to end
with the deterministic fake embedding provider; the rest of the suite runs
with the flag forced off (see ``core.test_runner``).
"""

from __future__ import annotations

import shutil
import tempfile
import time
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TransactionTestCase, override_settings

from documents.models import Document
from documents.services import embeddings as embeddings_module
from documents.services import vector_store as vector_store_module
from documents.tests.helpers import make_docx

User = get_user_model()

POLL_TIMEOUT_SECONDS = 15


def wait_for_status(document: Document, target_statuses: set[str]) -> Document:
    """Poll until *document* reaches one of *target_statuses* (or timeout)."""
    deadline = time.time() + POLL_TIMEOUT_SECONDS
    while time.time() < deadline:
        document.refresh_from_db()
        if document.status in target_statuses:
            return document
        # Gentle polling: frequent shared-cache reads can starve the
        # background writer on SQLite.
        time.sleep(0.25)
    document.refresh_from_db()
    return document


class AsyncProcessingTestBase(TransactionTestCase):
    """TransactionTestCase because the worker thread needs committed rows."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._chroma_tmpdir = tempfile.mkdtemp(prefix="chroma_async_")
        cls._override = override_settings(
            EMBEDDINGS_PROVIDER="fake",
            CHROMA_PERSIST_DIRECTORY=cls._chroma_tmpdir,
            DOCUMENT_PROCESSING_ASYNC=True,
        )
        cls._override.enable()
        cls.addClassCleanup(cls._override.disable)
        cls.addClassCleanup(shutil.rmtree, cls._chroma_tmpdir, ignore_errors=True)

    def setUp(self) -> None:
        super().setUp()
        embeddings_module.reset_cache()
        vector_store_module.reset_cache()
        vector_store_module.reset_collection()
        self.user = User.objects.create_user(
            username="asyncuser", password="pw-123456", is_staff=True
        )
        from rest_framework.authtoken.models import Token
        from rest_framework.test import APIClient

        token = Token.objects.create(user=self.user)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")


class AsyncDocumentFlowTests(AsyncProcessingTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.payload = make_docx(
            headings=["Handbook"],
            paragraphs=["Annual leave entitlement is 26 days per year."] * 25,
        )

    def test_upload_returns_pending_and_completes_in_background(self):
        file = ContentFile(self.payload, name="handbook.docx")
        response = self.client.post(
            "/api/v1/documents/", data={"file": file, "title": "Handbook"}, format="multipart"
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], Document.Status.PENDING)
        document_id = response.data["id"]

        document = wait_for_status(
            Document.objects.get(pk=document_id),
            {Document.Status.COMPLETED, Document.Status.FAILED},
        )
        self.assertEqual(document.status, Document.Status.COMPLETED)
        self.assertGreater(document.chunk_count, 0)
        self.assertEqual(
            vector_store_module.get_indexed_chunk_count(), document.chunk_count
        )

    def test_content_edit_reindexes_in_background(self):
        # Create synchronously through the service (already completed).
        from documents.services import document_service

        document = document_service.create_document(
            text="Original content about leave days. " * 20, title="Leave"
        )
        old_chunk_ids = set(document.chunks.values_list("id", flat=True))

        response = self.client.patch(
            f"/api/v1/documents/{document.pk}/",
            {"content": "Edited content about deployment windows. " * 20},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], Document.Status.PENDING)

        document = wait_for_status(
            Document.objects.get(pk=document.pk),
            {Document.Status.COMPLETED, Document.Status.FAILED},
        )
        self.assertEqual(document.status, Document.Status.COMPLETED)
        self.assertIn("deployment windows", document.content)
        new_chunk_ids = set(document.chunks.values_list("id", flat=True))
        self.assertFalse(old_chunk_ids & new_chunk_ids)

    def test_reprocess_returns_202_and_completes(self):
        from documents.services import document_service

        document = document_service.create_document(
            text="Content to be reprocessed. " * 20, title="Repro"
        )
        document.status = Document.Status.FAILED
        document.error_message = "simulated earlier failure"
        document.save()

        response = self.client.post(f"/api/v1/documents/{document.pk}/reprocess/")
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.data["status"], Document.Status.PENDING)

        document = wait_for_status(
            Document.objects.get(pk=document.pk),
            {Document.Status.COMPLETED, Document.Status.FAILED},
        )
        self.assertEqual(document.status, Document.Status.COMPLETED)
        self.assertEqual(document.error_message, "")

    def test_background_worker_failure_marks_document_failed(self):
        from documents.services import document_service

        document = document_service.create_document(
            text="Content whose processing will fail. " * 20, title="Fails"
        )
        # The mock must be active before the job is queued, so the
        # background worker fails deterministically.
        with mock.patch(
            "documents.services.vector_store.add_chunks",
            side_effect=RuntimeError("embedding backend down"),
        ):
            response = self.client.post(f"/api/v1/documents/{document.pk}/reprocess/")
            self.assertEqual(response.status_code, 202)
            document = wait_for_status(
                Document.objects.get(pk=document.pk),
                {Document.Status.COMPLETED, Document.Status.FAILED},
            )
        self.assertEqual(document.status, Document.Status.FAILED)
        # replace_document_chunks translates the backend failure into a
        # DocumentProcessingError with a safe, actionable message.
        self.assertIn("could not be embedded", document.error_message.lower())
        self.assertEqual(document.chunks.count(), 0)


class ProcessingCommandsTests(TransactionTestCase):
    """Tests for the preload and recovery management commands."""

    def test_preload_is_a_noop_for_the_fake_provider(self):
        from io import StringIO

        with override_settings(EMBEDDINGS_PROVIDER="fake"):
            out = StringIO()
            call_command("preload_embedding_model", stdout=out)
        self.assertIn("no model files to prepare", out.getvalue())

    def test_preload_failure_raises_command_error(self):
        # When the embedding model cannot be loaded (e.g. no network for the
        # first download), the command must FAIL LOUDLY: the embedding-init
        # Compose service depends on a non-zero exit code so that the web
        # container is never started without a working model.
        from io import StringIO

        with override_settings(EMBEDDINGS_PROVIDER="fastembed"):
            with mock.patch(
                "documents.services.embeddings.get_embeddings",
                side_effect=RuntimeError("model unavailable"),
            ):
                with self.assertRaises(CommandError) as ctx:
                    call_command("preload_embedding_model", stdout=StringIO())
        self.assertIn("Embedding model initialization FAILED", str(ctx.exception))
        self.assertIn("model unavailable", str(ctx.exception))

    def test_reset_stale_processing_marks_interrupted_documents_failed(self):
        Document.objects.create(
            title="Stuck processing",
            content="Some content. " * 5,
            status=Document.Status.PROCESSING,
        )
        Document.objects.create(
            title="Stuck pending",
            content="Other content. " * 5,
            status=Document.Status.PENDING,
        )
        Document.objects.create(
            title="Completed stays",
            content="Finished content. " * 5,
            status=Document.Status.COMPLETED,
        )

        from io import StringIO

        out = StringIO()
        call_command("reset_stale_processing", stdout=out)
        self.assertIn("Reset 2 interrupted document(s)", out.getvalue())

        self.assertEqual(
            Document.objects.get(title="Stuck processing").status,
            Document.Status.FAILED,
        )
        stuck_pending = Document.objects.get(title="Stuck pending")
        self.assertEqual(stuck_pending.status, Document.Status.FAILED)
        self.assertIn("Reprocess this document", stuck_pending.error_message)
        self.assertEqual(
            Document.objects.get(title="Completed stays").status,
            Document.Status.COMPLETED,
        )

    def test_reset_stale_processing_without_victims(self):
        from io import StringIO

        out = StringIO()
        call_command("reset_stale_processing", stdout=out)
        self.assertIn("No interrupted documents", out.getvalue())

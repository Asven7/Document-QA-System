"""Tests for the document lifecycle service (DB + vector store consistency).

Documents are text-centric: they are created from a DOCX upload (text
extracted in memory, binary discarded) or from manually entered text, and
the text can be edited later. Runs with the deterministic fake embeddings
provider and a temporary Chroma directory (see
``core.testing.ChromaEnabledTestCase``).
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import override_settings

from core.testing import ChromaEnabledTestCase
from documents.exceptions import (
    ContentValidationError,
    DocxValidationError,
    DuplicateDocumentError,
)
from documents.models import Document, DocumentChunk
from documents.services import document_service, vector_store
from documents.tests.helpers import make_docx

User = get_user_model()


def docx_file(payload: bytes, name: str = "sample.docx") -> ContentFile:
    return ContentFile(payload, name=name)


class DocumentServiceTests(ChromaEnabledTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create_user(username="staffer", password="pw-123456")
        self.payload = make_docx(
            headings=["Handbook"],
            paragraphs=["Annual leave entitlement is 26 days per year."] * 30,
            table=(["Env", "Window"], [["Production", "Tuesday"]]),
        )

    # ------------------------------------------------------------------
    # create from DOCX upload
    # ------------------------------------------------------------------
    def test_create_document_from_upload_processes_and_indexes(self):
        document = document_service.create_document(
            uploaded_file=docx_file(self.payload, "handbook.docx"),
            title="Handbook",
            user=self.user,
        )
        self.assertEqual(document.status, Document.Status.COMPLETED)
        self.assertEqual(document.uploaded_by, self.user)
        self.assertEqual(document.source, Document.Source.DOCX)
        self.assertEqual(document.original_filename, "handbook.docx")
        self.assertIn("Annual leave entitlement", document.content)
        self.assertGreater(document.chunk_count, 0)
        self.assertEqual(document.chunks.count(), document.chunk_count)
        self.assertEqual(vector_store.get_indexed_chunk_count(), document.chunk_count)
        self.assertEqual(len(document.content_hash), 64)

    def test_create_document_default_title_from_filename(self):
        document = document_service.create_document(
            uploaded_file=docx_file(self.payload, "my handbook.docx")
        )
        self.assertEqual(document.title, "my handbook")

    def test_upload_binary_is_not_persisted(self):
        # The DOCX binary is a transport format only: nothing may be stored
        # on disk and no FileField remains on the model.
        document = document_service.create_document(
            uploaded_file=docx_file(self.payload, "handbook.docx")
        )
        self.assertFalse(hasattr(document, "file"))
        from django.core.files.storage import default_storage

        self.assertFalse(default_storage.exists("documents/handbook.docx"))

    def test_duplicate_content_rejected_across_sources(self):
        document_service.create_document(
            uploaded_file=docx_file(self.payload, "first.docx")
        )
        with self.assertRaises(DuplicateDocumentError) as ctx:
            document_service.create_document(
                uploaded_file=docx_file(self.payload, "second.docx")
            )
        self.assertIn("identical content", str(ctx.exception))

    def test_invalid_extension_rejected(self):
        with self.assertRaises(DocxValidationError):
            document_service.create_document(
                uploaded_file=docx_file(b"plain text", "notes.txt")
            )

    def test_non_docx_zip_rejected(self):
        import io
        import zipfile

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("readme.txt", "hello")
        with self.assertRaises(DocxValidationError):
            document_service.create_document(
                uploaded_file=docx_file(buffer.getvalue(), "fake.docx")
            )

    def test_oversized_upload_rejected(self):
        with override_settings(UPLOAD_MAX_FILE_SIZE_MB=0):
            with self.assertRaises(DocxValidationError) as ctx:
                document_service.create_document(
                    uploaded_file=docx_file(self.payload, "big.docx")
                )
            self.assertIn("maximum upload size", str(ctx.exception))

    def test_non_zip_payload_rejected(self):
        # Bytes that are not a ZIP container at all are rejected before
        # anything is stored (the API surface shows the same behaviour).
        with self.assertRaises(DocxValidationError) as ctx:
            document_service.create_document(
                uploaded_file=docx_file(b"not a zip at all", "broken.docx")
            )
        self.assertIn("ZIP container", str(ctx.exception))

    def test_corrupt_docx_stored_with_failed_status_and_clear_error(self):
        # A truncated real DOCX passes the container checks; the deep parse
        # then fails and the document is stored with status=failed plus a
        # clear error message (and nothing indexed).
        truncated = make_docx(paragraphs=["Annual leave is 26 days."])[:150]
        document = document_service.create_document(
            uploaded_file=docx_file(truncated, "broken.docx")
        )
        self.assertEqual(document.status, Document.Status.FAILED)
        self.assertIn("DOCX", document.error_message)
        self.assertEqual(document.chunk_count, 0)
        self.assertEqual(document.chunks.count(), 0)
        self.assertEqual(vector_store.get_indexed_chunk_count(), 0)
        self.assertIsNone(document.content_hash)

    # ------------------------------------------------------------------
    # create from manually entered text
    # ------------------------------------------------------------------
    def test_create_document_from_text(self):
        document = document_service.create_document(
            text="کارکنان می‌توانند حداکثر ۲ روز در هفته دورکار کار کنند.",
            title="سیاست دورکاری",
            user=self.user,
        )
        self.assertEqual(document.status, Document.Status.COMPLETED)
        self.assertEqual(document.source, Document.Source.TEXT)
        self.assertEqual(document.original_filename, "")
        self.assertIn("دورکار", document.content)
        self.assertGreater(document.chunk_count, 0)
        self.assertEqual(document.chunks.count(), document.chunk_count)
        self.assertEqual(vector_store.get_indexed_chunk_count(), document.chunk_count)

    def test_create_from_text_default_title_from_first_line(self):
        document = document_service.create_document(
            text="Deployment windows for production are Tuesday and Thursday.\nMore text."
        )
        self.assertEqual(
            document.title, "Deployment windows for production are Tuesday and Thursday."
        )

    def test_create_from_text_long_first_line_title_is_truncated(self):
        document = document_service.create_document(text="x" * 200)
        self.assertEqual(document.title, "x" * 60 + "...")

    def test_create_with_neither_file_nor_text_rejected(self):
        with self.assertRaises(ContentValidationError):
            document_service.create_document()

    def test_create_with_both_file_and_text_rejected(self):
        with self.assertRaises(ContentValidationError):
            document_service.create_document(
                uploaded_file=docx_file(self.payload, "a.docx"), text="some text"
            )

    def test_empty_text_rejected(self):
        with self.assertRaises(ContentValidationError) as ctx:
            document_service.create_document(text="   \n  ")
        self.assertIn("must not be empty", str(ctx.exception))

    def test_too_long_text_rejected(self):
        with override_settings(DOCUMENT_MAX_TEXT_CHARS=100):
            with self.assertRaises(ContentValidationError) as ctx:
                document_service.create_document(text="a" * 101)
            self.assertIn("maximum of 100 characters", str(ctx.exception))

    def test_duplicate_text_rejected(self):
        document_service.create_document(text="Annual leave is 26 days per year.")
        with self.assertRaises(DuplicateDocumentError):
            document_service.create_document(text="Annual leave is 26 days per year.")

    # ------------------------------------------------------------------
    # update / reprocess
    # ------------------------------------------------------------------
    def test_update_title_only_keeps_chunks(self):
        document = document_service.create_document(
            uploaded_file=docx_file(self.payload, "handbook.docx")
        )
        chunk_count = document.chunk_count
        updated = document_service.update_document(document, title="Renamed handbook")
        self.assertEqual(updated.title, "Renamed handbook")
        self.assertEqual(updated.status, Document.Status.COMPLETED)
        self.assertEqual(updated.chunk_count, chunk_count)

    def test_update_content_replaces_chunks_and_vectors(self):
        document = document_service.create_document(
            uploaded_file=docx_file(self.payload, "handbook.docx")
        )
        old_chunk_ids = list(document.chunks.values_list("id", flat=True))
        updated = document_service.update_document(
            document,
            content="Completely different content about deployment windows. " * 20,
        )
        self.assertEqual(updated.status, Document.Status.COMPLETED)
        self.assertIn("deployment windows", updated.content)
        self.assertEqual(updated.source, Document.Source.DOCX)  # provenance kept
        new_chunk_ids = list(updated.chunks.values_list("id", flat=True))
        self.assertFalse(set(old_chunk_ids) & set(new_chunk_ids))
        self.assertEqual(
            vector_store.get_indexed_chunk_count(), updated.chunk_count
        )

    def test_update_content_failure_keeps_content_and_clears_index(self):
        # Processing failures must never destroy the user's text: only the
        # generated chunks/vectors are cleared.
        from unittest.mock import patch

        document = document_service.create_document(text="Annual leave is 26 days. " * 20)
        document.content = "New edited content about deployment windows. " * 10
        document.save()
        with patch(
            "documents.services.vector_store.add_chunks",
            side_effect=RuntimeError("embedding backend down"),
        ):
            result = document_service.reprocess_document(document)
        self.assertEqual(result.status, Document.Status.FAILED)
        self.assertIn("New edited content", result.content)
        self.assertEqual(result.chunks.count(), 0)
        self.assertEqual(vector_store.get_indexed_chunk_count(), 0)

    def test_update_content_duplicate_rejected(self):
        document = document_service.create_document(text="Document A content. " * 5)
        document_service.create_document(text="Document B content. " * 5)
        with self.assertRaises(DuplicateDocumentError):
            document_service.update_document(
                document, content="Document B content. " * 5
            )

    def test_reprocess_reindexes_document(self):
        document = document_service.create_document(
            uploaded_file=docx_file(self.payload, "handbook.docx")
        )
        result = document_service.reprocess_document(document)
        self.assertEqual(result.status, Document.Status.COMPLETED)
        self.assertEqual(
            vector_store.get_indexed_chunk_count(), result.chunk_count
        )

    # ------------------------------------------------------------------
    # delete
    # ------------------------------------------------------------------
    def test_delete_document_removes_chunks_and_vectors(self):
        document = document_service.create_document(
            uploaded_file=docx_file(self.payload, "handbook.docx")
        )
        self.assertGreater(vector_store.get_indexed_chunk_count(), 0)
        document_service.delete_document(document)
        self.assertFalse(Document.objects.filter(pk=document.pk).exists())
        self.assertEqual(DocumentChunk.objects.count(), 0)
        self.assertEqual(vector_store.get_indexed_chunk_count(), 0)

    def test_delete_via_queryset_signal_cleanup(self):
        # Admin bulk delete goes through QuerySet.delete(); the pre_delete
        # signal must clean up vectors in that path as well.
        document = document_service.create_document(
            uploaded_file=docx_file(self.payload, "handbook.docx")
        )
        Document.objects.filter(pk=document.pk).delete()
        self.assertEqual(vector_store.get_indexed_chunk_count(), 0)

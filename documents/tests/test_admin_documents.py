"""Tests for the Django Admin screens of the documents app."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase
from django.urls import reverse

from documents.models import Document
from documents.tests.helpers import make_docx

User = get_user_model()


class DocumentAdminTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        # The admin tests never index vectors for valid uploads because they
        # assert page rendering; a failed document is used for listing pages.
        # Where processing is needed, the fake embedding provider is enabled
        # per test below.

    def setUp(self) -> None:
        import shutil
        import tempfile

        from django.test import override_settings

        from documents.services import embeddings as emb, vector_store as vs

        self.tmpdir = tempfile.mkdtemp(prefix="admin_chroma_")
        override = override_settings(
            EMBEDDINGS_PROVIDER="fake", CHROMA_PERSIST_DIRECTORY=self.tmpdir
        )
        override.enable()
        self.addCleanup(override.disable)
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.addCleanup(vs.reset_cache)
        self.addCleanup(emb.reset_cache)
        emb.reset_cache()
        vs.reset_cache()

        self.staff = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="admin-pw-123"
        )
        self.client.force_login(self.staff)
        self.payload = make_docx(paragraphs=["Annual leave is 26 days."] * 20)

    def test_changelist_renders(self):
        response = self.client.get(reverse("admin:documents_document_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Documents")

    def test_add_form_renders(self):
        response = self.client.get(reverse("admin:documents_document_add"))
        self.assertEqual(response.status_code, 200)

    def test_upload_via_admin_creates_processed_document(self):
        url = reverse("admin:documents_document_add")
        with ContentFile(self.payload, name="handbook.docx") as upload:
            response = self.client.post(
                url, {"title": "Handbook", "file": upload}, follow=True
            )
        self.assertEqual(response.status_code, 200)
        document = Document.objects.get(title="Handbook")
        self.assertEqual(document.status, Document.Status.COMPLETED)
        self.assertEqual(document.source, Document.Source.DOCX)
        self.assertGreater(document.chunk_count, 0)
        self.assertEqual(document.uploaded_by, self.staff)

    def test_create_text_document_via_admin(self):
        url = reverse("admin:documents_document_add")
        response = self.client.post(
            url,
            {"title": "Remote work policy", "content": "Employees may work remotely "
             "up to 2 days per week. " * 10},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        document = Document.objects.get(title="Remote work policy")
        self.assertEqual(document.status, Document.Status.COMPLETED)
        self.assertEqual(document.source, Document.Source.TEXT)
        self.assertEqual(document.original_filename, "")
        self.assertIn("remotely", document.content)
        self.assertGreater(document.chunk_count, 0)

    def test_create_without_file_or_content_shows_error(self):
        url = reverse("admin:documents_document_add")
        response = self.client.post(url, {"title": "Empty"}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "one of the two is required")
        self.assertEqual(Document.objects.count(), 0)

    def test_create_with_both_file_and_content_shows_error(self):
        url = reverse("admin:documents_document_add")
        with ContentFile(self.payload, name="handbook.docx") as upload:
            response = self.client.post(
                url,
                {"title": "Both", "file": upload, "content": "some text"},
                follow=True,
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "not both")
        self.assertEqual(Document.objects.count(), 0)

    def test_upload_invalid_file_shows_error_and_saves_nothing(self):
        url = reverse("admin:documents_document_add")
        with ContentFile(b"not a zip", name="bad.docx") as upload:
            response = self.client.post(
                url, {"title": "Bad", "file": upload}, follow=True
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ZIP container")
        self.assertEqual(Document.objects.count(), 0)

    def test_change_page_shows_content_and_status(self):
        from documents.services import document_service

        with ContentFile(self.payload, name="handbook.docx") as upload:
            document = document_service.create_document(
                uploaded_file=upload, title="Handbook"
            )
        url = reverse("admin:documents_document_change", args=[document.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Annual leave is 26 days.")
        self.assertContains(response, "Completed")

    def test_edit_content_via_admin_reindexes(self):
        from documents.services import document_service, vector_store

        with ContentFile(self.payload, name="handbook.docx") as upload:
            document = document_service.create_document(
                uploaded_file=upload, title="Handbook"
            )
        old_chunk_ids = set(document.chunks.values_list("id", flat=True))
        url = reverse("admin:documents_document_change", args=[document.pk])
        response = self.client.post(
            url,
            {"title": "Handbook", "content": "Replaced content about on-call duties. " * 15},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "was indexed")
        self.assertContains(response, "was updated")
        document.refresh_from_db()
        self.assertIn("on-call duties", document.content)
        self.assertEqual(document.status, Document.Status.COMPLETED)
        new_chunk_ids = set(document.chunks.values_list("id", flat=True))
        self.assertFalse(old_chunk_ids & new_chunk_ids)
        self.assertEqual(vector_store.get_indexed_chunk_count(), document.chunk_count)

    def test_delete_via_change_page_removes_document_and_vectors(self):
        # The Admin "Delete" button on the change page must remove the row,
        # its chunks AND its vectors (regression test for the deletion path).
        from documents.services import document_service, vector_store

        with ContentFile(self.payload, name="handbook.docx") as upload:
            document = document_service.create_document(
                uploaded_file=upload, title="Doomed"
            )
        self.assertGreater(vector_store.get_indexed_chunk_count(), 0)

        url = reverse("admin:documents_document_change", args=[document.pk])
        delete_url = reverse("admin:documents_document_delete", args=[document.pk])
        response = self.client.get(delete_url)  # delete confirmation page
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Are you sure")
        self.assertContains(response, "Doomed")

        response = self.client.post(
            delete_url, {"post": "yes"}, follow=True  # confirmed deletion
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Document.objects.filter(pk=document.pk).exists())
        self.assertEqual(vector_store.get_indexed_chunk_count(), 0)

    def test_delete_via_changelist_bulk_action(self):
        from documents.services import document_service, vector_store

        with ContentFile(self.payload, name="handbook.docx") as upload:
            document = document_service.create_document(
                uploaded_file=upload, title="Bulk doomed"
            )
        url = reverse("admin:documents_document_changelist")
        response = self.client.post(
            url,
            {
                "action": "delete_selected",
                "_selected_action": [str(document.pk)],
            },
        )
        # Django shows a confirmation page for the bulk delete...
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Are you sure")
        # ...and deleting requires the confirmation POST.
        response = self.client.post(
            url,
            {
                "action": "delete_selected",
                "_selected_action": [str(document.pk)],
                "post": "yes",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Document.objects.filter(pk=document.pk).exists())
        self.assertEqual(vector_store.get_indexed_chunk_count(), 0)

    def test_changelist_signals_processing_for_auto_refresh(self):
        # The document list refreshes itself while background processing is
        # running, so async status flips are visible without manual reloads.
        from documents.services import document_service

        url = reverse("admin:documents_document_changelist")

        document = document_service.create_document(
            text="Auto refresh probe. " * 10, title="Probe", process=False
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "http-equiv=\"refresh\"")

        document.status = Document.Status.COMPLETED
        document.save()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "http-equiv=\"refresh\"")

    def test_reprocess_bulk_action(self):
        from documents.services import document_service

        with ContentFile(self.payload, name="handbook.docx") as upload:
            document = document_service.create_document(
                uploaded_file=upload, title="Handbook"
            )
        url = reverse("admin:documents_document_changelist")
        response = self.client.post(
            url,
            {"action": "reprocess_selected_documents", "_selected_action": [str(document.pk)]},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "reprocessed successfully")
        document.refresh_from_db()
        self.assertEqual(document.status, Document.Status.COMPLETED)

    def test_chunk_changelist_renders_readonly(self):
        from documents.services import document_service

        with ContentFile(self.payload, name="handbook.docx") as upload:
            document = document_service.create_document(
                uploaded_file=upload, title="Handbook"
            )
        response = self.client.get(
            reverse("admin:documents_documentchunk_changelist"),
            {"document__id__exact": str(document.pk)},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Handbook")
        # No add permission is granted (chunks are generated data).
        add_url = reverse("admin:documents_documentchunk_add")
        response = self.client.get(add_url)
        self.assertEqual(response.status_code, 403)

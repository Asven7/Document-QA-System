"""API tests for document management endpoints."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient, APITestCase

from documents.models import Document
from documents.tests.helpers import make_docx

User = get_user_model()


class DocumentAPITestCase(APITestCase):
    def setUp(self) -> None:
        # These API tests do not need the vector store (uploads happen with a
        # corrupt/valid file but the vector layer is exercised in the service
        # tests); to keep them fast and isolated we keep the default settings.
        self.user = User.objects.create_user(
            username="apiuser", password="pw-123456", is_staff=True
        )
        self.token = Token.objects.create(user=self.user)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.token.key}")

    def _upload(self, payload: bytes, filename: str = "doc.docx", **extra):
        file = ContentFile(payload, name=filename)
        return self.client.post(
            "/api/v1/documents/",
            data={"file": file, **extra},
            format="multipart",
        )

    def _create_text(self, content: str, **extra):
        return self.client.post(
            "/api/v1/documents/",
            data={"content": content, **extra},
            format="json",
        )


class AuthenticationTests(DocumentAPITestCase):
    def test_endpoints_require_authentication(self):
        anonymous = APIClient()
        for method, url in [
            ("get", "/api/v1/documents/"),
            ("post", "/api/v1/documents/"),
            ("get", "/api/v1/questions/"),
            ("post", "/api/v1/questions/"),
        ]:
            response = getattr(anonymous, method)(url)
            self.assertEqual(
                response.status_code,
                status.HTTP_401_UNAUTHORIZED,
                f"{method.upper()} {url} should require auth",
            )

    def test_token_obtain_endpoint(self):
        response = self.client.post(
            "/api/auth/token/",
            {"username": "apiuser", "password": "pw-123456"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["token"], self.token.key)
        self.assertEqual(response.data["username"], "apiuser")

    def test_invalid_credentials_rejected(self):
        response = self.client.post(
            "/api/auth/token/",
            {"username": "apiuser", "password": "wrong"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class UploadValidationTests(DocumentAPITestCase):
    def test_reject_non_docx_extension(self):
        response = self._upload(b"plain text", "notes.txt")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Only .docx", str(response.data["file"]))

    def test_reject_empty_file(self):
        response = self._upload(b"", "empty.docx")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_reject_non_zip_payload_with_docx_extension(self):
        response = self._upload(b"definitely not a zip", "fake.docx")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("ZIP container", str(response.data["file"]))

    def test_corrupt_docx_accepted_but_marked_failed(self):
        # A truncated zip passes the magic-byte check; deep parsing then fails
        # and the document is stored with status=failed and a clear error.
        payload = make_docx(paragraphs=["hello world"])[:150]
        response = self._upload(payload, "truncated.docx")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], "failed")
        self.assertIn("DOCX", response.data["error_message"])


class DocumentCreateTests(DocumentAPITestCase):
    def setUp(self) -> None:
        super().setUp()
        from django.test import override_settings
        import shutil, tempfile

        from documents.services import embeddings as emb, vector_store as vs

        self._tmpdir = tempfile.mkdtemp(prefix="api_create_chroma_")
        settings_ctx = override_settings(
            EMBEDDINGS_PROVIDER="fake", CHROMA_PERSIST_DIRECTORY=self._tmpdir
        )
        settings_ctx.enable()
        self.addCleanup(settings_ctx.disable)
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)
        emb.reset_cache()
        vs.reset_cache()
        self.addCleanup(emb.reset_cache)
        self.addCleanup(vs.reset_cache)

        self.payload = make_docx(
            headings=["Handbook"],
            paragraphs=["Annual leave entitlement is 26 days per year."] * 25,
            table=(["Env", "Window"], [["Production", "Tuesday"]]),
        )

    def test_create_from_json_text(self):
        response = self._create_text(
            "کارکنان می‌توانند حداکثر ۲ روز در هفته دورکار کار کنند.\nجلسات حضوری است.",
            title="سیاست دورکاری",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], "completed")
        self.assertEqual(response.data["source"], "text")
        self.assertEqual(response.data["title"], "سیاست دورکاری")
        self.assertEqual(response.data["original_filename"], "")
        self.assertGreater(response.data["chunk_count"], 0)
        # Exact UTF-8 round trip through the JSON response.
        self.assertIn("دورکار", response.data["content"])

    def test_create_from_text_default_title_is_first_line(self):
        response = self._create_text("Deployment windows are Tuesday and Thursday.\nMore.")
        self.assertEqual(
            response.data["title"], "Deployment windows are Tuesday and Thursday."
        )

    def test_create_from_multipart_text_field(self):
        response = self.client.post(
            "/api/v1/documents/",
            data={"content": "Text sent as a form field.", "title": "Form text"},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["source"], "text")

    def test_create_with_neither_file_nor_content_rejected(self):
        response = self.client.post("/api/v1/documents/", data={}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("non_field_errors", response.data)

    def test_create_with_both_file_and_content_rejected(self):
        file = ContentFile(make_docx(paragraphs=["hello"]), name="doc.docx")
        response = self.client.post(
            "/api/v1/documents/",
            data={"file": file, "content": "some text"},
            format="multipart",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("not both", str(response.data))

    def test_create_with_empty_content_rejected(self):
        response = self._create_text("   \n ")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("must not be empty", str(response.data["content"]))

    def test_create_with_too_long_content_rejected(self):
        from django.test import override_settings

        with override_settings(DOCUMENT_MAX_TEXT_CHARS=100):
            response = self._create_text("a" * 101)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(
            any("maximum" in str(value) for value in response.data.values())
        )

    def test_create_duplicate_text_returns_409(self):
        first = self._create_text("Annual leave is 26 days per year.")
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        second = self._create_text("Annual leave is 26 days per year.")
        self.assertEqual(second.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("identical content", second.data["detail"])


class DocumentCRUDTests(DocumentAPITestCase):
    def setUp(self) -> None:
        super().setUp()
        from django.test import override_settings
        import tempfile, shutil

        # Use an isolated fake-embedding vector store for CRUD flows.
        self._tmpdir = tempfile.mkdtemp(prefix="api_chroma_")
        settings_ctx = override_settings(
            EMBEDDINGS_PROVIDER="fake", CHROMA_PERSIST_DIRECTORY=self._tmpdir
        )
        settings_ctx.enable()
        self.addCleanup(settings_ctx.disable)
        self.addCleanup(shutil.rmtree, self._tmpdir, ignore_errors=True)

        from documents.services import embeddings as emb, vector_store as vs

        emb.reset_cache()
        vs.reset_cache()
        self.addCleanup(emb.reset_cache)
        self.addCleanup(vs.reset_cache)

        self.payload = make_docx(
            headings=["Handbook"],
            paragraphs=["Annual leave entitlement is 26 days per year."] * 25,
            table=(["Env", "Window"], [["Production", "Tuesday"]]),
        )

    def test_upload_list_detail_flow(self):
        response = self._upload(self.payload, "handbook.docx", title="Handbook")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], "completed")
        self.assertEqual(response.data["title"], "Handbook")
        self.assertEqual(response.data["source"], "docx")
        self.assertGreater(response.data["chunk_count"], 0)
        self.assertIn("Annual leave", response.data["content"])
        document_id = response.data["id"]

        listing = self.client.get("/api/v1/documents/")
        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        self.assertEqual(listing.data["count"], 1)
        self.assertNotIn("content", listing.data["results"][0])

        detail = self.client.get(f"/api/v1/documents/{document_id}/")
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.data["id"], document_id)

    def test_upload_duplicate_returns_409(self):
        self._upload(self.payload, "one.docx")
        response = self._upload(self.payload, "two.docx")
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("identical content", response.data["detail"])

    def test_update_title(self):
        document_id = self._upload(self.payload, "handbook.docx").data["id"]
        response = self.client.patch(
            f"/api/v1/documents/{document_id}/", {"title": "New title"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["title"], "New title")
        self.assertEqual(response.data["status"], "completed")

    def test_update_content_reindexes(self):
        from documents.services import vector_store

        document_id = self._create_text("Original content about leave days. " * 20).data["id"]
        old_chunk_ids = set(
            Document.objects.get(pk=document_id).chunks.values_list("id", flat=True)
        )
        response = self.client.patch(
            f"/api/v1/documents/{document_id}/",
            {"content": "Brand new content about deployment windows. " * 20},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "completed")
        self.assertIn("deployment windows", response.data["content"])
        document = Document.objects.get(pk=document_id)
        new_chunk_ids = set(document.chunks.values_list("id", flat=True))
        self.assertFalse(old_chunk_ids & new_chunk_ids)
        self.assertEqual(vector_store.get_indexed_chunk_count(), document.chunk_count)

    def test_update_content_duplicate_rejected(self):
        self._create_text("Content of document B. " * 10)
        other_id = self._create_text("Content of document A. " * 10).data["id"]
        response = self.client.patch(
            f"/api/v1/documents/{other_id}/",
            {"content": "Content of document B. " * 10},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_update_content_empty_rejected(self):
        document_id = self._create_text("Some content. " * 10).data["id"]
        response = self.client.patch(
            f"/api/v1/documents/{document_id}/", {"content": "   "}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_update_requires_at_least_one_field(self):
        document_id = self._create_text("Some content. " * 10).data["id"]
        response = self.client.patch(
            f"/api/v1/documents/{document_id}/", {}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_delete_document(self):
        document_id = self._upload(self.payload, "handbook.docx").data["id"]
        response = self.client.delete(f"/api/v1/documents/{document_id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(Document.objects.count(), 0)

    def test_reprocess_action(self):
        document_id = self._upload(self.payload, "handbook.docx").data["id"]
        response = self.client.post(f"/api/v1/documents/{document_id}/reprocess/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "completed")

    def test_download_file_action_removed(self):
        # The system stores text only; the binary file download endpoint no
        # longer exists.
        document_id = self._upload(self.payload, "handbook.docx").data["id"]
        response = self.client.get(f"/api/v1/documents/{document_id}/file/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_list_filter_by_status_validation(self):
        response = self.client.get("/api/v1/documents/?status=nonsense")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_list_filter_by_status(self):
        self._upload(self.payload, "handbook.docx")
        response = self.client.get("/api/v1/documents/?status=completed")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
        response = self.client.get("/api/v1/documents/?status=failed")
        self.assertEqual(response.data["count"], 0)

    def test_unknown_document_returns_404(self):
        response = self.client.get("/api/v1/documents/00000000-0000-0000-0000-000000000000/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

"""API tests for question submission and history."""

from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import override_settings
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient, APITestCase

from core.testing import ChromaEnabledTestCase
from documents.services import document_service
from documents.tests.helpers import make_docx
from questions.models import Question

User = get_user_model()


class QuestionAPITests(ChromaEnabledTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user = User.objects.create_user(
            username="asker", password="pw-123456", is_staff=True
        )
        self.token = Token.objects.create(user=self.user)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.token.key}")

        self.document = document_service.create_document(
            uploaded_file=ContentFile(
                make_docx(
                    headings=["Leave policy"],
                    paragraphs=[
                        "Annual leave entitlement is 26 days per calendar year "
                        "for all full-time employees. "
                    ]
                    * 6,
                ),
                name="leave.docx",
            ),
            title="Leave policy",
        )

    def _submit(self, payload):
        return self.client.post("/api/v1/questions/", payload, format="json")

    def _submit_with_fake_llm(self, payload, answer="26 days per year. [Source 1]"):
        with override_settings(OPENROUTER_API_KEY="test-key-not-a-real-secret"):
            fake = FakeListChatModel(responses=[answer])
            with patch("questions.services.rag.get_chat_model", return_value=fake):
                return self._submit(payload)

    # ------------------------------------------------------------------
    # Submission
    # ------------------------------------------------------------------
    def test_submit_question_returns_grounded_answer(self):
        response = self._submit_with_fake_llm(
            {"question_text": "How many days of annual leave?"}
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], "completed")
        self.assertEqual(response.data["answer_text"], "26 days per year. [Source 1]")
        self.assertEqual(response.data["model"], "meta-llama/llama-3.3-70b-instruct:free")
        self.assertTrue(response.data["sources"])
        self.assertEqual(response.data["asked_by"], "asker")

    def test_submit_persian_question_utf8_round_trip(self):
        persian_question = "هر کارمند چند روز مرخصی استحقاقی دارد؟"
        persian_answer = "هر کارمند ۲۶ روز مرخصی دارد. [Source 1]"
        response = self._submit_with_fake_llm(
            {"question_text": persian_question}, answer=persian_answer
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["question_text"], persian_question)
        self.assertEqual(response.data["answer_text"], persian_answer)
        # Exact UTF-8 bytes must survive the JSON round trip.
        self.assertIn(persian_question.encode("utf-8"), response.content)
        self.assertIn(persian_answer.encode("utf-8"), response.content)

    def test_submit_without_llm_key_returns_failed_question(self):
        response = self._submit({"question_text": "How many days of annual leave?"})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], "failed")
        self.assertIn("OPENROUTER_API_KEY", response.data["error_message"])

    def test_submit_with_document_scope(self):
        response = self._submit_with_fake_llm(
            {
                "question_text": "How many days of annual leave?",
                "document_ids": [str(self.document.pk)],
            }
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["document_scope"], [str(self.document.pk)])
        self.assertEqual(
            response.data["sources"][0]["document_id"], str(self.document.pk)
        )

    def test_submit_with_unknown_document_ids_rejected(self):
        response = self._submit(
            {
                "question_text": "How many days?",
                "document_ids": ["00000000-0000-0000-0000-000000000000"],
            }
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Unknown document id", str(response.data["document_ids"]))

    def test_submit_with_empty_document_ids_list_rejected(self):
        response = self._submit(
            {"question_text": "How many days?", "document_ids": []}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_submit_validation_errors(self):
        response = self._submit({"question_text": "ab"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        response = self._submit({})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        response = self._submit({"question_text": "x" * 2001})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------
    def test_history_list_and_detail(self):
        self._submit_with_fake_llm({"question_text": "First question about leave?"})
        self._submit_with_fake_llm({"question_text": "Second question about leave?"})

        listing = self.client.get("/api/v1/questions/")
        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        self.assertEqual(listing.data["count"], 2)
        self.assertIn("answer_text", listing.data["results"][0])

        question_id = listing.data["results"][0]["id"]
        detail = self.client.get(f"/api/v1/questions/{question_id}/")
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.data["id"], question_id)
        self.assertTrue(detail.data["sources"])

    def test_history_filter_by_status(self):
        self._submit({"question_text": "Failing question about leave?"})  # no key
        listing = self.client.get("/api/v1/questions/?status=failed")
        self.assertEqual(listing.status_code, status.HTTP_200_OK)
        self.assertEqual(listing.data["count"], 1)
        listing = self.client.get("/api/v1/questions/?status=completed")
        self.assertEqual(listing.data["count"], 0)

    def test_history_search(self):
        self._submit_with_fake_llm({"question_text": "unique-zephyr question?"})
        listing = self.client.get("/api/v1/questions/?search=unique-zephyr")
        self.assertEqual(listing.data["count"], 1)

    def test_history_records_are_immutable(self):
        question = Question.objects.create(question_text="manual")
        response = self.client.patch(
            f"/api/v1/questions/{question.pk}/", {"answer_text": "tampered"}, format="json"
        )
        self.assertIn(response.status_code, {405, 404})  # no update endpoint
        response = self.client.delete(f"/api/v1/questions/{question.pk}/")
        self.assertIn(response.status_code, {405, 404})  # no delete endpoint

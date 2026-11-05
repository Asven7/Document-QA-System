"""Tests for the question admin screens, including the ask-question page."""

from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import override_settings
from django.urls import reverse
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from core.testing import ChromaEnabledTestCase
from documents.services import document_service
from documents.tests.helpers import make_docx
from questions.models import Question

User = get_user_model()


class QuestionAdminTests(ChromaEnabledTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.staff = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="admin-pw-123"
        )
        self.client.force_login(self.staff)
        self.document = document_service.create_document(
            uploaded_file=ContentFile(
                make_docx(paragraphs=["Annual leave is 26 days."] * 8),
                name="leave.docx",
            ),
            title="Leave policy",
        )

    def test_changelist_renders_with_ask_button(self):
        response = self.client.get(reverse("admin:questions_question_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ask a question")

    def test_ask_question_page_renders(self):
        response = self.client.get(reverse("admin:questions_question_ask"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ask a question")
        self.assertContains(response, "Leave policy")  # selectable document

    def test_ask_question_submits_and_redirects_to_result(self):
        url = reverse("admin:questions_question_ask")
        with override_settings(OPENROUTER_API_KEY="test-key-not-a-real-secret"):
            fake = FakeListChatModel(responses=["26 days per year. [Source 1]"])
            with patch("questions.services.rag.get_chat_model", return_value=fake):
                response = self.client.post(
                    url, {"question_text": "How many leave days?"}, follow=True
                )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "answered successfully")
        question = Question.objects.get(question_text="How many leave days?")
        self.assertEqual(question.status, Question.Status.COMPLETED)
        self.assertEqual(question.answer_text, "26 days per year. [Source 1]")
        self.assertEqual(question.asked_by, self.staff)

    def test_ask_question_without_llm_key_shows_error(self):
        url = reverse("admin:questions_question_ask")
        response = self.client.post(
            url, {"question_text": "How many leave days?"}, follow=True
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "could not be answered")
        question = Question.objects.get(question_text="How many leave days?")
        self.assertEqual(question.status, Question.Status.FAILED)
        self.assertIn("OPENROUTER_API_KEY", question.error_message)

    def test_ask_question_with_document_scope(self):
        url = reverse("admin:questions_question_ask")
        with override_settings(OPENROUTER_API_KEY="test-key-not-a-real-secret"):
            fake = FakeListChatModel(responses=["26 days. [Source 1]"])
            with patch("questions.services.rag.get_chat_model", return_value=fake):
                self.client.post(
                    url,
                    {
                        "question_text": "How many leave days?",
                        "document_scope": [str(self.document.pk)],
                    },
                    follow=True,
                )
        question = Question.objects.get(question_text="How many leave days?")
        self.assertIn(self.document, question.document_scope.all())

    def test_question_detail_shows_sources_table(self):
        with override_settings(OPENROUTER_API_KEY="test-key-not-a-real-secret"):
            fake = FakeListChatModel(responses=["26 days. [Source 1]"])
            with patch("questions.services.rag.get_chat_model", return_value=fake):
                question = Question.objects.create(question_text="placeholder")
                question.delete()
                from questions.services.rag import run_rag_pipeline

                question = run_rag_pipeline(question_text="How many leave days?")
        url = reverse("admin:questions_question_change", args=[question.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Retrieved sources")
        self.assertContains(response, "Leave policy")
        self.assertContains(response, "RRF score")

    def test_non_staff_user_cannot_access(self):
        regular = User.objects.create_user(username="regular", password="pw-123456")
        self.client.force_login(regular)
        response = self.client.get(reverse("admin:questions_question_changelist"))
        self.assertEqual(response.status_code, 302)  # redirected to admin login

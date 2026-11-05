"""Tests for the RAG pipeline: prompts, grounded answers, error handling."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import openai
from django.core.files.base import ContentFile
from django.test import override_settings
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable, RunnableLambda

from core.testing import ChromaEnabledTestCase
from documents.services import document_service
from documents.tests.helpers import make_docx
from questions.models import Question
from questions.services.prompts import build_context_blocks, get_rag_prompt
from questions.services.rag import (
    localized_insufficient_context_answer,
    run_rag_pipeline,
)
from questions.services.retrieval import RetrievedChunk

FAKE_ANSWER = (
    "Employees receive 26 days of annual leave per calendar year. [Source 1]"
)


def _http_error(exception_class, status_code: int):
    request = httpx.Request("POST", "https://openrouter.test/v1")
    response = httpx.Response(status_code, request=request)
    return exception_class("provider error", response=response, body=None)


class FailingChatModel:
    """Chat model stub that raises a canned exception on invoke."""

    def __init__(self, exception_factory) -> None:
        self._exception_factory = exception_factory

    def invoke(self, messages, config=None, **kwargs):
        raise self._exception_factory()


def _ensure_runnable(model):
    """Wrap plain stub models for LangChain's ``|`` composition.

    ``RunnableSequence`` only accepts Runnable objects (langchain-core >= 1.x
    rejects arbitrary classes). Real LangChain chat models pass through
    unchanged; lightweight test stubs are wrapped in a ``RunnableLambda`` that
    forwards to their ``invoke`` and lets exceptions propagate untouched.
    """
    if isinstance(model, Runnable):
        return model
    return RunnableLambda(model.invoke)


def _run_with_model(model) -> Question:
    """Run the pipeline with a stubbed LLM and a fake API key configured."""
    with override_settings(OPENROUTER_API_KEY="test-key-not-a-real-secret"):
        with patch(
            "questions.services.rag.get_chat_model",
            return_value=_ensure_runnable(model),
        ):
            return run_rag_pipeline(question_text="How many leave days?")


class PromptTests(ChromaEnabledTestCase):
    def test_system_prompt_contains_grounding_rules(self):
        messages = get_rag_prompt().invoke({"context": "CTX", "question": "Q"})
        system_text = str(messages.messages[0].content)
        self.assertIn("ONLY with information that is present in the context", system_text)
        self.assertIn("same language", system_text.lower())
        self.assertIn("UNTRUSTED", system_text)
        self.assertIn("instructions", system_text.lower())

    def test_context_blocks_contain_source_labels_and_metadata(self):
        chunk = RetrievedChunk(
            chunk_id="c1",
            document_id="d1",
            document_title="Handbook",
            chunk_index=3,
            content="Annual leave is 26 days.",
            rrf_score=0.03,
        )
        block = build_context_blocks([chunk])[0]
        self.assertIn("[Source 1]", block)
        self.assertIn("Handbook", block)
        self.assertIn("document_id: d1", block)
        self.assertIn("chunk_index: 3", block)
        self.assertIn("Annual leave is 26 days.", block)


class InsufficientContextTests(ChromaEnabledTestCase):
    def test_no_documents_english_notice_without_llm_call(self):
        with patch("questions.services.rag.get_chat_model") as mock_model:
            question = run_rag_pipeline(question_text="What is the policy?")
            mock_model.assert_not_called()  # honest answer, no fake LLM call
        self.assertEqual(question.status, Question.Status.COMPLETED)
        self.assertEqual(
            question.answer_text,
            "The uploaded documents do not contain enough information to answer this question.",
        )
        self.assertEqual(question.sources, [])
        self.assertEqual(question.model, "")

    def test_no_documents_persian_notice(self):
        question = run_rag_pipeline(
            question_text="سیاست دورکاری شرکت چیست و چند روز در هفته مجاز است؟"
        )
        self.assertEqual(question.status, Question.Status.COMPLETED)
        self.assertEqual(
            question.answer_text,
            "اسناد بارگذاری‌شده حاوی اطلاعات کافی برای پاسخ به این پرسش نیستند.",
        )

    def test_localized_message_language_selection(self):
        self.assertEqual(
            localized_insufficient_context_answer("How many days off?"),
            "The uploaded documents do not contain enough information to answer this question.",
        )
        self.assertIn(
            "اسناد",
            localized_insufficient_context_answer("مرخصی چیست؟"),
        )


class GroundedAnswerTests(ChromaEnabledTestCase):
    def setUp(self) -> None:
        super().setUp()
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

    def test_successful_answer_stored_with_sources(self):
        question = _run_with_model(FakeListChatModel(responses=[FAKE_ANSWER]))
        self.assertEqual(question.status, Question.Status.COMPLETED)
        self.assertEqual(question.answer_text, FAKE_ANSWER)
        self.assertEqual(
            question.model, "meta-llama/llama-3.3-70b-instruct:free"
        )
        self.assertTrue(question.sources)
        source = question.sources[0]
        self.assertEqual(source["document_id"], str(self.document.pk))
        self.assertEqual(source["document_title"], "Leave policy")
        self.assertIn("rrf_score", source)
        self.assertIn("chunk_index", source)

    def test_persian_question_persian_fake_answer(self):
        persian_answer = "هر کارمند ۲۶ روز مرخصی استحقاقی دارد. [Source 1]"
        with override_settings(OPENROUTER_API_KEY="test-key-not-a-real-secret"):
            fake = FakeListChatModel(responses=[persian_answer])
            with patch("questions.services.rag.get_chat_model", return_value=fake):
                question = run_rag_pipeline(
                    question_text="هر کارمند چند روز مرخصی استحقاقی دارد؟"
                )
        self.assertEqual(question.status, Question.Status.COMPLETED)
        self.assertEqual(question.answer_text, persian_answer)
        self.assertTrue(question.sources)
        self.assertEqual(question.sources[0]["document_id"], str(self.document.pk))

    def test_document_content_is_forwarded_as_context_text(self):
        # The retrieval context must end up inside the human message as plain
        # quoted text; the injection paragraph is content, not instructions.
        captured: dict = {}

        class CapturingModel:
            def invoke(self, messages, config=None, **kwargs):
                captured["text"] = str(messages.messages[-1].content)
                return AIMessage(content="stub answer")

        _run_with_model(CapturingModel())
        self.assertIn("CONTEXT:", captured["text"])
        self.assertIn("[Source 1]", captured["text"])
        self.assertIn("QUESTION:", captured["text"])
        self.assertIn("Annual leave entitlement", captured["text"])

    def test_scope_restricts_sources(self):
        other = document_service.create_document(
            uploaded_file=ContentFile(
                make_docx(paragraphs=["Deployment window is Tuesday."] * 6),
                name="deploy.docx",
            ),
            title="Deployment guide",
        )
        question = _run_with_model(FakeListChatModel(responses=[FAKE_ANSWER]))
        # (sanity: without scope both documents exist)
        self.assertEqual(question.document_scope.count(), 0)

        with override_settings(OPENROUTER_API_KEY="test-key-not-a-real-secret"):
            fake = FakeListChatModel(responses=[FAKE_ANSWER])
            with patch("questions.services.rag.get_chat_model", return_value=fake):
                scoped = run_rag_pipeline(
                    question_text="How many leave days?",
                    document_ids=[other.pk],
                )
        self.assertEqual(
            [str(pk) for pk in scoped.document_scope.values_list("id", flat=True)],
            [str(other.pk)],
        )


class LLMErrorHandlingTests(ChromaEnabledTestCase):
    def setUp(self) -> None:
        super().setUp()
        document_service.create_document(
            uploaded_file=ContentFile(
                make_docx(paragraphs=["Annual leave is 26 days."] * 6),
                name="leave.docx",
            ),
            title="Leave policy",
        )

    def test_missing_api_key_reports_configuration_error(self):
        question = run_rag_pipeline(question_text="How many leave days?")
        self.assertEqual(question.status, Question.Status.FAILED)
        self.assertIn("OPENROUTER_API_KEY", question.error_message)
        self.assertIn("openrouter.ai", question.error_message)
        # Retrieved sources are preserved for transparency.
        self.assertTrue(question.sources)
        self.assertEqual(question.answer_text, "")

    def test_invalid_api_key_reports_friendly_error(self):
        question = _run_with_model(
            FailingChatModel(lambda: _http_error(openai.AuthenticationError, 401))
        )
        self.assertEqual(question.status, Question.Status.FAILED)
        self.assertIn("rejected the configured API key", question.error_message)
        self.assertNotIn("test-key-not-a-real-secret", question.error_message)

    def test_rate_limit_reports_friendly_error(self):
        question = _run_with_model(
            FailingChatModel(lambda: _http_error(openai.RateLimitError, 429))
        )
        self.assertEqual(question.status, Question.Status.FAILED)
        self.assertIn("rate limit", question.error_message.lower())

    def test_timeout_reports_friendly_error(self):
        def timeout_factory():
            request = httpx.Request("POST", "https://openrouter.test/v1")
            return openai.APITimeoutError(request=request)

        question = _run_with_model(FailingChatModel(timeout_factory))
        self.assertEqual(question.status, Question.Status.FAILED)
        self.assertIn("timeout", question.error_message.lower())

    def test_model_not_found_reports_friendly_error(self):
        question = _run_with_model(
            FailingChatModel(lambda: _http_error(openai.NotFoundError, 404))
        )
        self.assertEqual(question.status, Question.Status.FAILED)
        self.assertIn("not available", question.error_message)

    def test_empty_llm_response_reports_friendly_error(self):
        class EmptyModel:
            def invoke(self, messages, config=None, **kwargs):
                return AIMessage(content="   ")

        question = _run_with_model(EmptyModel())
        self.assertEqual(question.status, Question.Status.FAILED)
        self.assertIn("empty response", question.error_message.lower())

    def test_unexpected_exception_reports_generic_error(self):
        class ExplodingModel:
            def invoke(self, messages, config=None, **kwargs):
                raise RuntimeError("internal provider crash with secret data")

        question = _run_with_model(ExplodingModel())
        self.assertEqual(question.status, Question.Status.FAILED)
        self.assertIn("unexpected error", question.error_message.lower())
        self.assertNotIn("secret data", question.error_message)

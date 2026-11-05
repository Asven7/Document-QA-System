"""End-to-end RAG pipeline orchestration.

Request lifecycle (``run_rag_pipeline``):

1. Persist the question with status ``processing``.
2. Run hybrid retrieval (semantic + BM25 + RRF), optionally restricted to
   the requested document ids.
3. Store the retrieved sources on the question record.
4. If nothing was retrieved, store a deterministic, localized
   "insufficient context" answer without calling the LLM.
5. Otherwise build the grounded prompt and generate the answer through
   LangChain (``prompt | ChatOpenAI | StrOutputParser``).
6. Persist answer, sources, model name and final status.

Every failure mode (missing key, invalid key, rate limit, timeout, empty
response, unexpected error) leaves the question record in status ``failed``
with a safe, human-readable error message - retrieved sources are kept for
transparency.
"""

from __future__ import annotations

import logging

from django.contrib.auth.models import AnonymousUser
from langchain_core.output_parsers import StrOutputParser

from core.text import persian_ratio
from questions.models import Question
from questions.services.llm import (
    LLMEmptyResponseError,
    LLMError,
    LLMNotConfiguredError,
    get_chat_model,
    get_llm_model_name,
    translate_llm_exception,
)
from questions.services.prompts import (
    INSUFFICIENT_CONTEXT_ANSWER_EN,
    INSUFFICIENT_CONTEXT_ANSWER_FA,
    build_context_blocks,
    get_rag_prompt,
)
from questions.services.retrieval import RetrievedChunk, retrieve_chunks

logger = logging.getLogger(__name__)


def localized_insufficient_context_answer(question_text: str) -> str:
    """Choose the insufficient-context message in the question's language."""
    if persian_ratio(question_text) >= 0.2:
        return INSUFFICIENT_CONTEXT_ANSWER_FA
    return INSUFFICIENT_CONTEXT_ANSWER_EN


def _sources_payload(retrieved: list[RetrievedChunk]) -> list[dict]:
    """Serialize retrieved chunks for storage on the question record."""
    return [
        {
            "document_id": chunk.document_id,
            "document_title": chunk.document_title,
            "chunk_id": chunk.chunk_id,
            "chunk_index": chunk.chunk_index,
            "rrf_score": chunk.rrf_score,
            "semantic_rank": chunk.semantic_rank,
            "semantic_distance": chunk.semantic_distance,
            "lexical_rank": chunk.lexical_rank,
        }
        for chunk in retrieved
    ]


def _generate_answer_with_llm(question_text: str, retrieved: list[RetrievedChunk]) -> str:
    """Run the LangChain chain: prompt | ChatOpenAI | StrOutputParser."""
    chat_model = get_chat_model()
    chain = get_rag_prompt() | chat_model | StrOutputParser()
    try:
        answer = chain.invoke(
            {
                "context": "\n\n---\n\n".join(build_context_blocks(retrieved)),
                "question": question_text,
            }
        )
    except LLMError:
        raise
    except Exception as exc:
        raise translate_llm_exception(exc) from exc
    if not answer or not str(answer).strip():
        raise LLMEmptyResponseError(
            "The LLM returned an empty response. Try submitting the question again."
        )
    return str(answer).strip()


def run_rag_pipeline(
    *,
    question_text: str,
    document_ids: list | None = None,
    user=None,
) -> Question:
    """Execute the full RAG workflow and persist the result.

    Args:
        question_text: the user's question (original text).
        document_ids: optional list of Document pks restricting the search.
        user: the submitting user (or ``None`` for anonymous/system calls).

    Returns:
        The persisted :class:`Question`, always in a terminal status
        (``completed`` or ``failed``).
    """
    if user is not None and isinstance(user, AnonymousUser):
        user = None

    question = Question.objects.create(
        question_text=question_text.strip(),
        status=Question.Status.PROCESSING,
        asked_by=user,
    )
    if document_ids:
        question.document_scope.set(document_ids)

    try:
        retrieved = retrieve_chunks(question_text, document_ids=document_ids)
        question.sources = _sources_payload(retrieved)

        if not retrieved:
            # Honest, cheap, localized: no LLM call when there is no context.
            question.answer_text = localized_insufficient_context_answer(question_text)
            question.model = ""
            question.status = Question.Status.COMPLETED
            question.error_message = ""
            logger.info("Question %s: no context found; stored localized notice.", question.pk)
        else:
            answer = _generate_answer_with_llm(question_text, retrieved)
            question.answer_text = answer
            question.model = get_llm_model_name()
            question.status = Question.Status.COMPLETED
            question.error_message = ""
            logger.info("Question %s answered (model=%s, sources=%d).",
                        question.pk, question.model, len(retrieved))
    except LLMNotConfiguredError as exc:
        question.status = Question.Status.FAILED
        question.error_message = str(exc)
        logger.warning("Question %s failed: LLM not configured.", question.pk)
    except LLMError as exc:
        question.status = Question.Status.FAILED
        question.error_message = str(exc)
        logger.warning("Question %s failed: %s", question.pk, str(exc)[:200])
    except Exception:
        question.status = Question.Status.FAILED
        question.error_message = (
            "An unexpected error occurred while answering the question. "
            "Check the server logs for details."
        )
        logger.exception("Question %s failed unexpectedly.", question.pk)

    question.save()
    return question

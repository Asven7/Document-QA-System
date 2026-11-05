"""LLM integration through OpenRouter using LangChain.

Design
------
OpenRouter exposes an OpenAI-compatible HTTP API, so the integration uses
``langchain_openai.ChatOpenAI`` with ``base_url`` pointing at OpenRouter.
Everything is configurable via environment variables (see
``config/settings.py``): ``OPENROUTER_API_KEY``, ``OPENROUTER_BASE_URL``,
``OPENROUTER_MODEL``, ``LLM_TEMPERATURE``, ``LLM_MAX_TOKENS``,
``LLM_TIMEOUT_SECONDS``, ``LLM_MAX_RETRIES``.

Error handling
--------------
All provider failures are translated into domain exceptions with messages
that are safe to show to API clients - provider payloads, URLs and keys are
never included. The application starts and serves documents/admin without a
key; only question answering requires one, in which case
``LLMNotConfiguredError`` explains exactly how to configure it.
"""

from __future__ import annotations

import logging

import openai
from django.conf import settings
from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)

# Attribution headers recommended by OpenRouter so requests can be traced
# back to this application in the OpenRouter dashboard.
_DEFAULT_HEADERS = {
    "HTTP-Referer": "https://github.com/document-qa-system",
    "X-Title": "Document QA System",
}


class LLMError(Exception):
    """Base class for LLM failures with an API-client-safe message."""


class LLMNotConfiguredError(LLMError):
    """Raised when question answering is requested without an API key."""


class LLMAuthenticationError(LLMError):
    """The configured API key was rejected by the provider."""


class LLMRateLimitError(LLMError):
    """The provider rate limit was hit."""


class LLMTimeoutError(LLMError):
    """The provider did not answer in time."""


class LLMConnectionError(LLMError):
    """The provider could not be reached."""


class LLMEmptyResponseError(LLMError):
    """The model returned an empty answer."""


class LLMRequestError(LLMError):
    """Any other provider-side or unexpected failure."""


def is_llm_configured() -> bool:
    """Return True when an OpenRouter API key is present."""
    return bool(settings.OPENROUTER_API_KEY)


def get_llm_model_name() -> str:
    """Return the configured model identifier (reported in health/API)."""
    return settings.OPENROUTER_MODEL


def get_chat_model() -> ChatOpenAI:
    """Build the LangChain chat model for OpenRouter.

    Raises:
        LLMNotConfiguredError: when ``OPENROUTER_API_KEY`` is not set.
    """
    if not is_llm_configured():
        raise LLMNotConfiguredError(
            "The LLM is not configured. Create a free API key at "
            "https://openrouter.ai/, then set OPENROUTER_API_KEY (and optionally "
            "OPENROUTER_MODEL) in your .env file and restart the application. "
            "See README section 10 for step-by-step instructions."
        )
    return ChatOpenAI(
        model=settings.OPENROUTER_MODEL,
        api_key=settings.OPENROUTER_API_KEY,
        base_url=settings.OPENROUTER_BASE_URL,
        temperature=settings.LLM_TEMPERATURE,
        max_tokens=settings.LLM_MAX_TOKENS,
        timeout=settings.LLM_TIMEOUT_SECONDS,
        max_retries=settings.LLM_MAX_RETRIES,
        default_headers=_DEFAULT_HEADERS,
    )


def translate_llm_exception(exc: Exception) -> LLMError:
    """Map an exception raised while calling the LLM to a safe domain error."""
    if isinstance(exc, openai.AuthenticationError):
        return LLMAuthenticationError(
            "The LLM provider rejected the configured API key. Verify that "
            "OPENROUTER_API_KEY is valid and has not been revoked."
        )
    if isinstance(exc, openai.RateLimitError):
        return LLMRateLimitError(
            "The LLM provider rate limit was reached. Free models on OpenRouter "
            "are rate limited - wait a moment and try again, or configure a "
            "different model via OPENROUTER_MODEL."
        )
    if isinstance(exc, openai.APITimeoutError):
        return LLMTimeoutError(
            "The LLM provider did not respond within the configured timeout "
            f"({settings.LLM_TIMEOUT_SECONDS} seconds). Try again or increase "
            "LLM_TIMEOUT_SECONDS."
        )
    if isinstance(exc, openai.APIConnectionError):
        return LLMConnectionError(
            "The LLM provider could not be reached. Check your network "
            "connection and the OPENROUTER_BASE_URL setting."
        )
    if isinstance(exc, openai.NotFoundError):
        return LLMRequestError(
            "The configured model is not available on the provider. Choose a "
            "current free model from https://openrouter.ai/collections/free-models "
            "and set OPENROUTER_MODEL accordingly."
        )
    if isinstance(exc, openai.APIStatusError):
        return LLMRequestError(
            "The LLM provider returned an error (HTTP "
            f"{exc.status_code if hasattr(exc, 'status_code') else 'unknown'}). "
            "The question was not answered."
        )
    logger.exception("Unexpected LLM failure")
    return LLMRequestError(
        "An unexpected error occurred while contacting the LLM provider. "
        "Check the server logs for details."
    )

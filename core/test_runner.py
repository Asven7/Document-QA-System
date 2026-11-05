"""Custom test runner that isolates the suite from the developer's environment.

Two responsibilities:

* **Media isolation** - the document upload flow writes the uploaded DOCX to
  ``MEDIA_ROOT``. Files on disk are not part of the per-test database
  transaction, so without isolation every upload test would leave a
  ``.docx`` file behind in the real ``media/`` folder. This runner points
  ``MEDIA_ROOT`` at a temporary directory for the whole test run and deletes
  it afterwards.

* **LLM isolation** - ``config/settings.py`` loads the project ``.env`` so
  that local runs and Docker behave identically. The automated suite must
  never depend on (or accidentally spend) a real OpenRouter key, so the
  runner forces a deterministic LLM configuration: no API key and the
  documented default model. Tests that need a configured LLM override these
  settings explicitly (they use fake chat models and dummy keys).

Vector-store and embedding isolation stays the responsibility of
:class:`core.testing.ChromaEnabledTestCase` (per-test-case overrides),
because different test classes need different embedding providers.
"""

from __future__ import annotations

import shutil
import tempfile

from django.test.runner import DiscoverRunner
from django.test.utils import override_settings

# The documented default model; tests that assert on the model name rely on
# this value instead of on the developer's ``.env``.
_TEST_DEFAULT_MODEL = "meta-llama/llama-3.3-70b-instruct:free"


class IsolatedMediaRunner(DiscoverRunner):
    """A ``DiscoverRunner`` with isolated media storage and LLM settings."""

    def setup_test_environment(self, **kwargs) -> None:
        super().setup_test_environment(**kwargs)
        self._media_tmpdir = tempfile.mkdtemp(prefix="media_test_")
        self._settings_override = override_settings(
            MEDIA_ROOT=self._media_tmpdir,
            OPENROUTER_API_KEY="",
            OPENROUTER_MODEL=_TEST_DEFAULT_MODEL,
            # Synchronous document processing keeps API/Admin assertions
            # deterministic (the async queue has its own dedicated tests).
            DOCUMENT_PROCESSING_ASYNC=False,
        )
        self._settings_override.enable()

    def teardown_test_environment(self, **kwargs) -> None:
        self._settings_override.disable()
        shutil.rmtree(self._media_tmpdir, ignore_errors=True)
        super().teardown_test_environment(**kwargs)

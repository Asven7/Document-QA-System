"""Shared helpers for the automated test suite.

The test suite must never download embedding models or call external LLM APIs.
Tests therefore run with the deterministic ``fake`` embeddings provider and a
throwaway Chroma directory; LLM behaviour is exercised through LangChain's
``FakeListChatModel`` and stub models.
"""

from __future__ import annotations

import shutil
import tempfile

from django.test import TestCase
from django.test.utils import override_settings


class ChromaEnabledTestCase(TestCase):
    """Base test case that isolates the vector store.

    * ``EMBEDDINGS_PROVIDER`` is switched to ``fake`` (deterministic,
      offline, no model download).
    * ``CHROMA_PERSIST_DIRECTORY`` points at a per-class temporary directory
      that is removed when the class finishes.
    """

    def setUp(self) -> None:
        super().setUp()
        # Invalidate the cached embeddings/vector-store singletons so that
        # the overrides below are guaranteed to take effect.
        from documents.services import embeddings as embeddings_module
        from documents.services import vector_store as vector_store_module

        embeddings_module.reset_cache()
        vector_store_module.reset_cache()
        # TestCase rolls the database back after every test, but Chroma has
        # no transaction support: leftover vectors from earlier tests would
        # still answer searches. Start every test with an empty collection.
        vector_store_module.reset_collection()

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._chroma_tmpdir = tempfile.mkdtemp(prefix="chroma_test_")
        cls._settings_override = override_settings(
            EMBEDDINGS_PROVIDER="fake",
            CHROMA_PERSIST_DIRECTORY=cls._chroma_tmpdir,
        )
        cls._settings_override.enable()
        cls.addClassCleanup(cls._settings_override.disable)
        cls.addClassCleanup(shutil.rmtree, cls._chroma_tmpdir, ignore_errors=True)

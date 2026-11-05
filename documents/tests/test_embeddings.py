"""Tests for the embedding service configuration."""

from __future__ import annotations

import unittest.mock

from django.test import SimpleTestCase, override_settings

from documents.services import embeddings as embeddings_module
from documents.services.embeddings import FastEmbedAdapter, reset_cache


class FastEmbedAdapterTests(SimpleTestCase):
    def tearDown(self) -> None:
        reset_cache()

    def test_model_is_loaded_with_the_configured_cache_dir(self):
        # Regression: FASTEMBED_CACHE_PATH used to be documented but never
        # passed to fastembed, so the ONNX model was cached in the (ephemeral
        # Docker) home directory instead of the persistent model_cache volume.
        adapter = FastEmbedAdapter(model_name="Qwen/Qwen3-Embedding-0.6B-Q")
        fake_instance = object()
        with override_settings(FASTEMBED_CACHE_PATH="/tmp/custom_model_cache"):
            with unittest.mock.patch(
                "fastembed.TextEmbedding", return_value=fake_instance
            ) as factory:
                model = adapter.model
        self.assertIs(model, fake_instance)
        factory.assert_called_once_with(
            model_name="Qwen/Qwen3-Embedding-0.6B-Q",
            cache_dir=str("/tmp/custom_model_cache"),
        )

    def test_operations_before_first_embed_do_not_load_the_model(self):
        # Deleting vectors or counting them must not trigger a model download.
        adapter = FastEmbedAdapter(model_name="Qwen/Qwen3-Embedding-0.6B-Q")
        self.assertIsNone(adapter._model)


class StaleHandleRetryTests(SimpleTestCase):
    """The vector store must self-heal when another process wrote to the collection."""

    def test_semantic_search_retries_once_after_stale_handle_error(self):
        from documents.services import vector_store

        # Drop the module cache before this test (other tests may have set it).
        vector_store.reset_cache()

        calls = {"n": 0}

        def flaky_operation():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError(
                    "Internal error: Error creating hnsw segment reader: Nothing found on disk"
                )
            return [("meta", 0.1)]

        with unittest.mock.patch.object(
            vector_store, "get_vector_store", return_value=object()
        ):
            with unittest.mock.patch.object(
                vector_store, "reset_cache", wraps=vector_store.reset_cache
            ) as reset_mock:
                results = vector_store._with_stale_handle_retry(flaky_operation)

        self.assertEqual(results, [("meta", 0.1)])
        self.assertEqual(calls["n"], 2)
        reset_mock.assert_called_once()

    def test_unrelated_errors_propagate_immediately(self):
        from documents.services import vector_store

        def broken_operation():
            raise ValueError("some other failure")

        with self.assertRaises(ValueError):
            vector_store._with_stale_handle_retry(broken_operation)

@override_settings(EMBEDDINGS_PROVIDER="fake")
class WarmupTests(SimpleTestCase):
    """warmup_embedding_model() must load + verify with one real embedding."""

    def tearDown(self) -> None:
        embeddings_module.reset_cache()

    def test_warmup_with_fake_provider_returns_dimension(self):
        from documents.services.embeddings import warmup_embedding_model

        dimension = warmup_embedding_model()
        self.assertEqual(dimension, 256)
        adapter = embeddings_module.get_embeddings()
        # The fake provider has no lazy loading; the real adapter exposes it.
        self.assertTrue(getattr(adapter, "is_loaded", True))

    def test_concurrent_first_use_loads_the_model_only_once(self):
        # The lazy load is guarded by a lock: two threads racing the first
        # access to `.model` must construct the TextEmbedding instance once.
        import threading

        from documents.services.embeddings import FastEmbedAdapter

        adapter = FastEmbedAdapter(model_name="Qwen/Qwen3-Embedding-0.6B-Q")
        self.assertFalse(adapter.is_loaded)

        loads = []

        def fake_factory(**kwargs):
            loads.append(kwargs)
            return object()

        start = threading.Barrier(2)
        results = []

        def touch():
            start.wait()
            results.append(adapter.model)
        with unittest.mock.patch("fastembed.TextEmbedding", side_effect=fake_factory):
            threads = [threading.Thread(target=touch) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)

        self.assertEqual(len(loads), 1, "model must be constructed exactly once")
        self.assertEqual(len(results), 2)
        # Both threads received the same, fully initialized instance.
        self.assertIs(results[0], results[1])
        self.assertTrue(adapter.is_loaded)

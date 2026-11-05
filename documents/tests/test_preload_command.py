"""Tests for the embedding-model initialization command (embedding-init)."""

from __future__ import annotations

import io
import unittest.mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from documents.services import embeddings


class PreloadModelCommandTests(SimpleTestCase):
    def tearDown(self) -> None:
        embeddings.reset_cache()

    def call_preload(self, *args) -> str:
        buffer = io.StringIO()
        call_command("preload_embedding_model", *args, stdout=buffer)
        return buffer.getvalue()

    @override_settings(EMBEDDINGS_PROVIDER="fake")
    def test_fake_provider_is_a_no_op(self):
        output = self.call_preload()
        self.assertIn("no model files to prepare", output)

    @override_settings(EMBEDDINGS_PROVIDER="fastembed")
    def test_fastembed_provider_downloads_loads_and_verifies(self):
        embeddings.reset_cache()
        fake_model = unittest.mock.MagicMock()
        fake_model.query_embed.return_value = iter([[0.1] * 8])
        with unittest.mock.patch(
            "fastembed.TextEmbedding", return_value=fake_model
        ) as factory:
            output = self.call_preload()
        # The exact configured model and cache dir are passed to fastembed.
        factory.assert_called_once_with(
            model_name="Qwen/Qwen3-Embedding-0.6B-Q",
            cache_dir=unittest.mock.ANY,
        )
        # A real forward pass verified the model.
        fake_model.query_embed.assert_called_once()
        self.assertIn("successfully initialized", output)
        self.assertIn("(vector dimension: 8)", output)

    @override_settings(EMBEDDINGS_PROVIDER="fastembed")
    def test_failure_raises_command_error_with_actionable_message(self):
        embeddings.reset_cache()
        with unittest.mock.patch(
            "fastembed.TextEmbedding",
            side_effect=RuntimeError("network unreachable"),
        ):
            with self.assertRaises(CommandError) as ctx:
                self.call_preload()
        self.assertIn("Embedding model initialization FAILED", str(ctx.exception))
        self.assertIn("network unreachable", str(ctx.exception))

    @override_settings(EMBEDDINGS_PROVIDER="fastembed")
    def test_skip_verify_loads_without_forward_pass(self):
        embeddings.reset_cache()
        fake_model = unittest.mock.MagicMock()
        with unittest.mock.patch("fastembed.TextEmbedding", return_value=fake_model):
            output = self.call_preload("--skip-verify")
        factory_calls = fake_model.embed.call_count
        self.assertEqual(factory_calls, 0)
        self.assertIn("successfully initialized", output)

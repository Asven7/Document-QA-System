"""Prepare (download if needed) and verify the configured embedding model.

This command is the heart of the ``embedding-init`` Docker Compose service:
it runs to completion BEFORE the Django web container is allowed to start
(``depends_on: service_completed_successfully``), so a model download can
never happen inside a document upload or a question request.

Behaviour (idempotent):

* Cache already valid  -> loads the model from the local cache and verifies
  it with one real embedding forward pass (no network needed when
  ``HF_HUB_OFFLINE=1`` is set; the init service itself keeps network access
  so it can repair an incomplete cache).
* Cache missing/partial -> downloads the model (about 1.1 GB for the
  default Qwen3 multilingual model) into ``FASTEMBED_CACHE_PATH`` (the
  persistent ``model_cache`` Docker volume) and then verifies it.
* Failure (no network, invalid files, bad configuration) -> exits non-zero
  with a clear, actionable error so ``docker compose up`` fails loudly
  instead of starting a web container that cannot embed documents.

The verification is a real embedding operation, not just a file-existence
check: a download that returned without error but produced unusable files
still fails here.
"""

from __future__ import annotations

import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = (
        "Download (if needed) and verify the configured embedding model into "
        "FASTEMBED_CACHE_PATH. Exits non-zero on any failure. Used by the "
        "embedding-init Compose service before the web container starts."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--skip-verify",
            action="store_true",
            help="Only ensure the model files are present; skip the embedding "
            "verification forward pass (not recommended).",
        )

    def handle(self, *args, **options) -> None:
        if settings.EMBEDDINGS_PROVIDER != "fastembed":
            self.stdout.write(
                f"Embeddings provider is '{settings.EMBEDDINGS_PROVIDER}' - "
                "no model files to prepare."
            )
            return

        from documents.services.embeddings import get_embeddings

        self.stdout.write("[1/3] Checking embedding model cache ...")
        self.stdout.write(
            f"      provider: {settings.EMBEDDINGS_PROVIDER} | "
            f"model: {settings.EMBEDDING_MODEL} | "
            f"cache: {settings.FASTEMBED_CACHE_PATH}"
        )
        self.stdout.write(
            "[2/3] Loading the model (downloads it on first use; "
            "subsequent runs reuse the persistent cache) ..."
        )
        started = time.time()
        try:
            embeddings = get_embeddings()
            if options["skip_verify"]:
                # Force the load without a forward pass (tests/special cases).
                _ = embeddings.model  # noqa: B018 - property triggers the load
                dimension = None
            else:
                dimension = len(embeddings.embed_query("preload warm-up"))
        except Exception as exc:
            raise CommandError(
                "Embedding model initialization FAILED: "
                f"{type(exc).__name__}: {exc}. "
                "The web container was NOT started (docker compose gating). "
                "Fix the cause above and re-run 'docker compose up'. If the "
                "cache is corrupt, remove the model_cache volume and retry."
            ) from exc
        elapsed = time.time() - started

        self.stdout.write(
            self.style.SUCCESS(
                f"[3/3] Embedding model successfully initialized in {elapsed:.1f}s"
                + (f" (vector dimension: {dimension})." if dimension else ".")
            )
        )

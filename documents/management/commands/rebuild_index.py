"""Rebuild the Chroma vector index from the database chunk table.

The PostgreSQL chunk table is the source of truth; this command resets the
Chroma collection and re-embeds every chunk of every successfully processed
document. Use it:

* after changing ``EMBEDDING_MODEL`` (vectors from different models must
  never be mixed),
* after a ``CHROMA_PERSIST_DIRECTORY`` wipe or migration to a new volume,
* to clean up stale vectors after an interrupted operation.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from documents.models import Document, DocumentChunk
from documents.services import vector_store


class Command(BaseCommand):
    help = (
        "Drop and rebuild the Chroma vector index from the database chunk table "
        "(re-embeds all chunks of completed documents)."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--skip-confirm",
            action="store_true",
            help="Do not ask for confirmation (useful in scripts).",
        )

    def handle(self, *args, **options) -> None:
        documents = Document.objects.filter(status=Document.Status.COMPLETED)
        chunk_count = DocumentChunk.objects.filter(document__in=documents).count()

        if chunk_count == 0:
            self.stdout.write(self.style.WARNING("No completed document chunks to index."))
            vector_store.reset_collection()
            return

        if not options["skip_confirm"]:
            answer = input(
                f"This will re-embed {chunk_count} chunk(s) from "
                f"{documents.count()} document(s) and replace the current vector "
                f"index. Continue? [y/N] "
            )
            if answer.strip().lower() not in {"y", "yes"}:
                self.stdout.write("Aborted.")
                return

        self.stdout.write("Dropping the existing vector collection...")
        vector_store.reset_collection()

        self.stdout.write(f"Re-embedding {chunk_count} chunk(s)...")
        indexed = 0
        for document in documents.prefetch_related("chunks"):
            chunks = list(document.chunks.all())
            vector_store.add_chunks(chunks)
            indexed += len(chunks)
            self.stdout.write(
                f"  indexed {len(chunks):3d} chunks from '{document.title}'"
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Rebuild complete: {indexed} chunk(s) indexed across "
                f"{documents.count()} document(s)."
            )
        )

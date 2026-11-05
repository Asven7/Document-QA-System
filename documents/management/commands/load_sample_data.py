"""Load the sample documents into the running system.

Creates (if not present) the English and Persian sample documents, runs the
full processing pipeline (extraction, chunking, embedding, indexing) and
prints suggested demo questions. Idempotent: documents are matched by their
content hash, so running the command twice does not create duplicates.
"""

from __future__ import annotations

from pathlib import Path

from django.core.files.base import File
from django.core.management.base import BaseCommand

from documents.models import Document
from documents.exceptions import DuplicateDocumentError
from documents.services import document_service
from sample_data.generate_sample_documents import (
    generate_english_sample,
    generate_persian_sample,
)

SAMPLE_QUESTIONS = [
    ("English", "What is the code review SLA for pull requests?"),
    ("English", "How many approvals are required for a production deployment?"),
    ("English (cross-lingual)", "How many days of annual leave do employees receive?"),
    ("Persian", "ساعات کاری شرکت چگونه است و هر هفته چند ساعت باید کار شود؟"),
    ("Persian", "طبق راهنما، مرخصی ازدواج چند روز است و چه مدرکی لازم دارد؟"),
    ("Persian", "بودجه آموزشی سالانه هر کارمند چقدر است؟"),
    ("Persian (cross-lingual)", "برای استقرار در محیط production چه تأییدهایی لازم است؟"),
]


class Command(BaseCommand):
    help = (
        "Generate and load the English and Persian sample documents, process "
        "them (extract, chunk, embed, index) and print demo questions."
    )

    def handle(self, *args, **options) -> None:
        sample_dir = Path(__file__).resolve().parents[3] / "sample_data"
        temp_dir = Path(__file__).resolve().parents[3] / "sample_data" / "generated"
        temp_dir.mkdir(parents=True, exist_ok=True)

        targets = [
            ("English sample", generate_english_sample(temp_dir / "english_sample.docx")),
            ("Persian sample", generate_persian_sample(temp_dir / "persian_sample.docx")),
        ]

        for label, path in targets:
            with path.open("rb") as handle:
                uploaded = File(handle, name=path.name)
                try:
                    document = document_service.create_document(
                        uploaded_file=uploaded,
                        title=(
                            "NovaCloud Backend Development Handbook"
                            if "english" in path.name
                            else "راهنمای منابع انسانی شرکت داده‌پردازان آرمان"
                        ),
                    )
                except DuplicateDocumentError as exc:
                    self.stdout.write(
                        f"{label}: already loaded as '{exc.existing_title}' "
                        f"(id: {exc.existing_id}) - skipped."
                    )
                    continue
                status_text = (
                    f"status: {document.status}, chunks: {document.chunk_count}"
                    if document.status == Document.Status.COMPLETED
                    else f"status: {document.status} ({document.error_message})"
                )
                self.stdout.write(self.style.SUCCESS(
                    f"{label}: loaded '{document.title}' (id: {document.pk}, {status_text})"
                ))

        self.stdout.write("")
        self.stdout.write("Suggested demo questions (submit via Admin or API):")
        for language, question in SAMPLE_QUESTIONS:
            self.stdout.write(f"  [{language}] {question}")
        self.stdout.write("")
        self.stdout.write(
            f"Sample files live in: {sample_dir}"
        )

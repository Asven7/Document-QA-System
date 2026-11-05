"""Tests for the chunking service (LangChain RecursiveCharacterTextSplitter)."""

from __future__ import annotations

from django.test import SimpleTestCase, override_settings

from documents.services.chunking import build_text_splitter, split_document_text

LONG_ENGLISH = (
    "The first policy paragraph explains the code review rules in detail. "
    "Every pull request needs two approvals. "
) * 12

LONG_PERSIAN = (
    "هر کارمند تمام‌وقت باید در هر هفته ۴۰ ساعت کار کند و ساعات کاری شرکت "
    "از شنبه تا چهارشنبه تعریف شده است. مرخصی استحقاقی با تأیید مدیر ثبت می‌شود. "
) * 12


class ChunkingTests(SimpleTestCase):
    @override_settings(CHUNK_SIZE=200, CHUNK_OVERLAP=40)
    def test_long_text_produces_multiple_chunks_within_limit(self):
        chunks = split_document_text(LONG_ENGLISH)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            # The splitter targets chunk_size; the final piece of a merged
            # sequence may slightly exceed it, so allow a small tolerance.
            self.assertLessEqual(len(chunk), 220)

    @override_settings(CHUNK_SIZE=200, CHUNK_OVERLAP=40)
    def test_consecutive_chunks_overlap(self):
        chunks = split_document_text(LONG_ENGLISH)
        for first, second in zip(chunks, chunks[1:]):
            tail = first[-15:]
            self.assertIn(
                tail.strip()[-8:],
                second,
                "Expected the next chunk to repeat part of the previous chunk.",
            )

    def test_short_text_returns_single_chunk(self):
        chunks = split_document_text("A single short paragraph.")
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], "A single short paragraph.")

    def test_empty_text_returns_no_chunks(self):
        self.assertEqual(split_document_text(""), [])
        self.assertEqual(split_document_text("   \n  "), [])

    @override_settings(CHUNK_SIZE=200, CHUNK_OVERLAP=40)
    def test_all_content_preserved_across_chunks(self):
        text = LONG_ENGLISH + " UNIQUE-TOKEN-42 " + LONG_PERSIAN
        chunks = split_document_text(text)
        joined = "\n".join(chunks)
        self.assertIn("UNIQUE-TOKEN-42", joined)
        for sentence in LONG_ENGLISH.split(". "):
            if sentence.strip():
                self.assertIn(sentence.strip(), joined)
        # Persian sentences survive chunking without corruption.
        self.assertIn("تمام‌وقت", joined)

    @override_settings(CHUNK_SIZE=200, CHUNK_OVERLAP=40)
    def test_persian_chunks_respect_size_and_keep_zwnj(self):
        chunks = split_document_text(LONG_PERSIAN)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 220)
        joined = "\n".join(chunks)
        self.assertIn("می‌شود", joined)  # ZWNJ inside the word survived
        self.assertIn("۴۰", joined)  # Persian digits survive

    @override_settings(CHUNK_SIZE=40, CHUNK_OVERLAP=5)
    def test_splitter_prefers_paragraph_boundaries(self):
        # With a chunk size smaller than the combined text, the split must
        # happen at the paragraph boundary (the hardest separator) instead
        # of breaking inside a sentence.
        text = "First paragraph here.\n\nSecond paragraph here."
        chunks = split_document_text(text)
        self.assertEqual(chunks, ["First paragraph here.", "Second paragraph here."])

    @override_settings(CHUNK_SIZE=300, CHUNK_OVERLAP=50)
    def test_splitter_configurable_from_settings(self):
        splitter = build_text_splitter()
        self.assertEqual(splitter._chunk_size, 300)
        self.assertEqual(splitter._chunk_overlap, 50)

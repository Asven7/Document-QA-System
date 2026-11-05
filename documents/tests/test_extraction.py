"""Tests for the DOCX extraction service."""

from __future__ import annotations

import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from documents.services.extraction import DocxExtractionError, extract_docx_text
from documents.tests.helpers import make_docx


class ExtractionTests(SimpleTestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp(prefix="docx_extract_")

    def _write(self, payload: bytes, name: str = "sample.docx") -> Path:
        path = Path(self.tmpdir) / name
        path.write_bytes(payload)
        return path

    def test_paragraphs_and_headings_extracted_in_order(self):
        path = self._write(
            make_docx(headings=["First heading"], paragraphs=["First paragraph.", "Second paragraph."])
        )
        text = extract_docx_text(path)
        self.assertIn("First heading", text)
        self.assertIn("First paragraph.", text)
        self.assertIn("Second paragraph.", text)
        self.assertLess(text.index("First heading"), text.index("First paragraph."))
        self.assertLess(text.index("First paragraph."), text.index("Second paragraph."))

    def test_table_content_extracted(self):
        table = (["Env", "Window"], [["Production", "Tue/Thu 09:00-11:00 UTC"]])
        path = self._write(make_docx(paragraphs=["Intro"], table=table))
        text = extract_docx_text(path)
        self.assertIn("Env | Window", text)
        self.assertIn("Production | Tue/Thu 09:00-11:00 UTC", text)

    def test_paragraph_separation_preserved(self):
        path = self._write(make_docx(paragraphs=["One", "Two"]))
        text = extract_docx_text(path)
        self.assertIn("One\n\nTwo", text)

    def test_persian_text_extracted_without_corruption(self):
        persian = (
            "هر کارمند تمام‌وقت در سال ۲۶ روز مرخصی استحقاقی دارد؛ "
            "رمز عبور باید هر ۹۰ روز تغییر کند."
        )
        path = self._write(make_docx(paragraphs=[persian]))
        text = extract_docx_text(path)
        self.assertIn(persian, text)
        # ZWNJ (U+200C) must survive extraction untouched.
        self.assertIn("تمام‌وقت", text)

    def test_persian_table_extracted(self):
        table = (["نوع مرخصی", "تعداد روز"], [["استحقاقی سالانه", "۲۶ روز"]])
        path = self._write(make_docx(table=table))
        text = extract_docx_text(path)
        self.assertIn("نوع مرخصی | تعداد روز", text)
        self.assertIn("استحقاقی سالانه | ۲۶ روز", text)

    def test_corrupt_docx_rejected_with_clear_error(self):
        path = self._write(b"this is definitely not a zip file", name="corrupt.docx")
        with self.assertRaises(DocxExtractionError) as ctx:
            extract_docx_text(path)
        self.assertIn("not a valid DOCX", ctx.exception.message)

    def test_truncated_docx_rejected(self):
        payload = make_docx(paragraphs=["hello"])[:200]  # cut inside the zip
        path = self._write(payload, name="truncated.docx")
        with self.assertRaises(DocxExtractionError):
            extract_docx_text(path)

    def test_empty_document_reports_no_text(self):
        path = self._write(make_docx())
        with self.assertRaises(DocxExtractionError) as ctx:
            extract_docx_text(path)
        self.assertIn("No extractable text", ctx.exception.message)

    def test_missing_file_reports_error(self):
        with self.assertRaises(DocxExtractionError) as ctx:
            extract_docx_text(Path(self.tmpdir) / "does-not-exist.docx")
        self.assertIn("missing from storage", ctx.exception.message)

    def test_extract_from_in_memory_stream(self):
        # Uploads are parsed in memory - the binary never needs a temp file.
        import io

        stream = io.BytesIO(make_docx(headings=["Streamed"], paragraphs=["In-memory body."]))
        text = extract_docx_text(stream)
        self.assertIn("Streamed", text)
        self.assertIn("In-memory body.", text)

    def test_stream_parse_failure_raises_clean_error(self):
        # Regression: a parse failure *after* the DOCX opened successfully used
        # to raise UnboundLocalError for in-memory streams (the logger referenced
        # a variable that only exists for path sources) instead of the intended
        # DocxExtractionError.
        import io
        import unittest.mock

        stream = io.BytesIO(make_docx(paragraphs=["Body that will fail to walk."]))
        with unittest.mock.patch(
            "documents.services.extraction.iter_block_items",
            side_effect=RuntimeError("simulated XML walk failure"),
        ):
            with self.assertRaises(DocxExtractionError) as ctx:
                extract_docx_text(stream)
        self.assertIn("could not be parsed", ctx.exception.message)

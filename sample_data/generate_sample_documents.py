"""Generate the English and Persian sample DOCX files.

Run standalone (from the project root)::

    python sample_data/generate_sample_documents.py [--output-dir sample_data]

The same generator functions are reused by the ``load_sample_data``
management command so the demo data is always reproducible.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running this file standalone (python sample_data/generate_sample_documents.py)
# by making the project root importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docx import Document
from docx.shared import Pt

from sample_data.content import (
    ENGLISH_DOCUMENT_BLOCKS,
    ENGLISH_DOCUMENT_TITLE,
    PERSIAN_DOCUMENT_BLOCKS,
    PERSIAN_DOCUMENT_TITLE,
)


def _add_block(doc: Document, block: tuple) -> None:
    """Add one structured content block to a python-docx document."""
    kind = block[0]
    if kind == "h1":
        doc.add_heading(block[1], level=1)
    elif kind == "h2":
        doc.add_heading(block[1], level=2)
    elif kind == "p":
        paragraph = doc.add_paragraph(block[1])
        # 12pt keeps RTL and LTR text comfortably readable after extraction.
        for run in paragraph.runs:
            run.font.size = Pt(12)
    elif kind == "table":
        headers, rows = block[1], block[2]
        table = doc.add_table(rows=1, cols=len(headers))
        table.style = "Light Grid Accent 1"
        for index, header in enumerate(headers):
            table.rows[0].cells[index].text = str(header)
        for row in rows:
            cells = table.add_row().cells
            for index, value in enumerate(row):
                cells[index].text = str(value)
    else:  # pragma: no cover - guarded by the content module itself
        raise ValueError(f"Unknown block kind: {kind!r}")


def build_english_document() -> Document:
    document = Document()
    for block in ENGLISH_DOCUMENT_BLOCKS:
        _add_block(document, block)
    return document


def build_persian_document() -> Document:
    document = Document()
    for block in PERSIAN_DOCUMENT_BLOCKS:
        _add_block(document, block)
    return document


def generate_english_sample(path: Path) -> Path:
    build_english_document().save(str(path))
    return path


def generate_persian_sample(path: Path) -> Path:
    build_persian_document().save(str(path))
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parent),
        help="Directory the .docx files are written to (default: sample_data/).",
    )
    args = parser.parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    english_path = generate_english_sample(output_dir / "english_sample.docx")
    persian_path = generate_persian_sample(output_dir / "persian_sample.docx")
    print(f"English sample written to {english_path}")
    print(f"Persian sample written to {persian_path}")


if __name__ == "__main__":
    main()

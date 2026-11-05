"""DOCX text extraction service.

The extractor walks the document body *in order* and collects:

* every non-empty paragraph (headings are ordinary paragraphs with a style),
* every table row, rendered as ``cell | cell | ...`` so that table content
  stays searchable and readable,

separating blocks with blank lines so that paragraph structure survives into
the chunking stage.

The input may be a filesystem path or an open binary stream - uploads are
parsed in memory and the DOCX binary is never persisted anywhere.

Notes and limitations (documented on purpose):
* Headers/footers, footnotes and embedded images are not extracted.
* Images are NOT OCR-processed: a scanned document will yield an empty
  extraction and the document will be marked as failed with a clear message.
* Persian/Arabic text passes through untouched (UTF-8 end to end).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import BinaryIO
from typing import Iterator
from typing import Union

from docx import Document as DocxDocument
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from documents.exceptions import DocxValidationError

logger = logging.getLogger(__name__)

DocxSource = Union[str, Path, BinaryIO]


class DocxExtractionError(Exception):
    """Raised when a DOCX file cannot be read or contains no usable text."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def iter_block_items(document: DocxDocument) -> Iterator[Paragraph | Table]:
    """Yield paragraphs and tables in their original document order."""
    body = document.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield Table(child, document)


def _table_to_text(table: Table) -> str:
    """Render a table as one line per row, cells joined with ``|``."""
    lines: list[str] = []
    for row in table.rows:
        cells = [cell.text.strip() for cell in row.cells]
        if any(cells):
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def extract_docx_text(source: DocxSource) -> str:
    """Extract the full text content of the DOCX file or stream *source*.

    Args:
        source: a filesystem path, or an open binary stream positioned at
            the start of a DOCX payload (e.g. an uploaded file).

    Raises:
        DocxExtractionError: if the file cannot be read, is corrupt,
            password protected or contains no extractable text.
    """
    try:
        if hasattr(source, "read"):
            document = DocxDocument(source)  # in-memory upload stream
        else:
            path = Path(source)
            if not path.exists():
                raise DocxExtractionError("The document file is missing from storage.")
            document = DocxDocument(str(path))
    except DocxExtractionError:
        raise
    except Exception as exc:  # python-docx raises a wide variety of errors
        name = getattr(source, "name", None) or str(source)
        logger.warning("DOCX open failed for %s: %s", name, type(exc).__name__)
        raise DocxExtractionError(
            "The file is not a valid DOCX document. It may be corrupt, "
            "password protected or not a real Word file."
        ) from exc

    blocks: list[str] = []
    try:
        for block in iter_block_items(document):
            if isinstance(block, Paragraph):
                text = block.text.strip()
                if text:
                    blocks.append(text)
            else:  # Table
                table_text = _table_to_text(block)
                if table_text:
                    blocks.append(table_text)
    except Exception as exc:
        logger.warning(
            "DOCX parse failed for %s: %s",
            getattr(source, "name", None) or "<unknown>",
            type(exc).__name__,
        )
        raise DocxExtractionError(
            "The DOCX file could not be parsed; its structure appears to be damaged."
        ) from exc

    extracted = "\n\n".join(blocks).strip()
    if not extracted:
        raise DocxExtractionError(
            "No extractable text was found in the document. Note that images "
            "are not OCR-processed; a scanned document cannot be indexed."
        )
    return extracted

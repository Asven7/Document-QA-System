"""Shared helpers for building DOCX test fixtures in-memory."""

from __future__ import annotations

from docx import Document


def make_docx(
    paragraphs: list[str] | None = None,
    headings: list[str] | None = None,
    table: tuple[list[str], list[list[str]]] | None = None,
) -> bytes:
    """Build a small DOCX file and return its bytes."""
    document = Document()
    for heading in headings or []:
        document.add_heading(heading, level=1)
    for paragraph in paragraphs or []:
        document.add_paragraph(paragraph)
    if table is not None:
        headers, rows = table
        docx_table = document.add_table(rows=1, cols=len(headers))
        docx_table.style = "Light Grid Accent 1"
        for index, header in enumerate(headers):
            docx_table.rows[0].cells[index].text = header
        for row in rows:
            cells = docx_table.add_row().cells
            for index, value in enumerate(row):
                cells[index].text = value
    import io

    buffer = io.BytesIO()
    document.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()

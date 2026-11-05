"""Text chunking service built on LangChain's recursive character splitter.

Strategy
--------
``RecursiveCharacterTextSplitter`` tries a list of separators from
"hard" to "soft" and only falls back to smaller units when a chunk would
otherwise exceed ``CHUNK_SIZE``:

1. blank line (paragraph boundary)
2. newline
3. sentence enders, both Latin (``. ! ?``) and Persian (``؟``) with a
   trailing space so sentence-splitting does not break decimals
4. Persian (``،``) and Latin (``,`` / ``;``) clause separators
5. single space
6. empty separator (last resort, cuts mid-word only if unavoidable)

Defaults: ``CHUNK_SIZE = 800`` characters, ``CHUNK_OVERLAP = 150``.
800 characters is about one to three paragraphs: large enough to keep a
complete idea together, and small enough that a retrieved chunk stays on
one topic. The default embedding model (Qwen3-Embedding, 32k-token context)
can accept much longer passages, but shorter chunks improve retrieval
precision. 150 characters of overlap (about one or two sentences) keeps
context across chunk borders. Both values are configurable via environment
variables.

Persian safety: the splitter operates on plain Unicode text; it never
modifies characters, so ZWNJ (``U+200C``) and Persian punctuation survive
chunking untouched. Chunk ids are stable UUIDs stored in the database, and
``chunk_index`` reflects the document order.
"""

from __future__ import annotations

from django.conf import settings
from langchain_text_splitters import RecursiveCharacterTextSplitter

# Separators shared by English and Persian content, ordered from hardest to
# softest boundary.
_SEPARATORS = [
    "\n\n",
    "\n",
    ". ",
    "! ",
    "? ",
    "؟ ",
    "؛ ",
    "; ",
    "، ",
    ", ",
    " ",
    "",
]


def build_text_splitter() -> RecursiveCharacterTextSplitter:
    """Build a splitter configured from Django settings."""
    return RecursiveCharacterTextSplitter(
        chunk_size=settings.CHUNK_SIZE,
        chunk_overlap=settings.CHUNK_OVERLAP,
        separators=_SEPARATORS,
        keep_separator=False,
        length_function=len,
        strip_whitespace=True,
    )


def split_document_text(text: str) -> list[str]:
    """Split *text* into chunks; returns an empty list for empty input."""
    if not text or not text.strip():
        return []
    return build_text_splitter().split_text(text)

"""Unicode-safe text utilities used by the retrieval pipeline.

The original document text is always stored untouched (UTF-8). The helpers in
this module build a *normalized* representation that is only used while
searching, so that Persian queries match documents regardless of common
encoding variants:

* Arabic ``ي`` (U+064A) and ``ك`` (U+0643) are mapped to their Persian
  counterparts ``ی`` (U+06CC) and ``ک`` (U+06A9).
* Arabic diacritics (tashkeel) and tatweel/kashida are removed.
* The zero-width non-joiner (ZWNJ, U+200C) is replaced with a regular space so
  that ``می‌شود`` and ``می شود`` tokenize identically. This matters because
  documents typed with different keyboards mix both forms.
* Persian/Arabic-Indic digits (۰-۹ / ٠-٩) are mapped to ASCII digits.
* Latin text is case-folded so the lexical channel is case-insensitive.
* Persian and Latin punctuation is stripped during tokenization.

None of these functions are applied to stored document or question text.
"""

from __future__ import annotations

import re
import unicodedata

# Characters that carry Arabic vocalization; they are dropped for search.
_ARABIC_DIACRITICS = re.compile(r"[\u064B-\u065F\u0670]")

# Tatweel / kashida elongation character.
_TATWEEL = "\u0640"

# Zero-width non-joiner used as a word separator in Persian compound words.
_ZWNJ = "\u200c"

# Persian (Extended Arabic-Indic) and Arabic-Indic digit tables.
_PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

# Arabic variants that must map to Persian code points for search.
_ARABIC_TO_PERSIAN = str.maketrans({"ي": "ی", "ك": "ک", "ة": "ه", "أ": "ا", "إ": "ا", "ؤ": "و"})

# Sequences of whitespace (including ZWNJ after substitution) collapse to one space.
_WHITESPACE = re.compile(r"\s+")

# Characters kept as token boundaries. ``\w`` already covers Persian and
# Arabic letters in Python 3 (Unicode-aware regex); anything else - including
# Persian punctuation such as ``،`` (U+060C), ``؟`` and ``؛`` - acts as a
# split point so punctuation is never glued onto search tokens.
_TOKEN_SPLIT = re.compile(r"[^\w]+")


def normalize_for_search(text: str) -> str:
    """Return a search-friendly copy of *text* (never modifies the input).

    The normalization is deliberately conservative: it only removes variants
    that are interchangeable in practice for Persian readers. It is applied
    exclusively inside the retrieval pipeline (lexical scoring and tokenized
    semantic queries); the stored ``extracted_text`` and ``content`` fields
    always keep the original bytes.
    """
    if not text:
        return ""
    normalized = unicodedata.normalize("NFC", text)
    normalized = normalized.translate(_ARABIC_TO_PERSIAN)
    normalized = _ARABIC_DIACRITICS.sub("", normalized)
    normalized = normalized.replace(_TATWEEL, "")
    # Treat ZWNJ as a soft space: both "می‌شود" and "می شود" produce the same tokens.
    normalized = normalized.replace(_ZWNJ, " ")
    normalized = normalized.translate(_PERSIAN_DIGITS).translate(_ARABIC_DIGITS)
    normalized = _WHITESPACE.sub(" ", normalized)
    return normalized.strip().casefold()


def tokenize_for_search(text: str) -> list[str]:
    """Split *text* into normalized lowercase/Persian tokens for lexical search."""
    normalized = normalize_for_search(text)
    if not normalized:
        return []
    return [token for token in _TOKEN_SPLIT.split(normalized) if token]


def persian_ratio(text: str) -> float:
    """Return the share of Persian/Arabic-script letters among all letters.

    Used to decide the language of deterministic (non-LLM) system answers,
    e.g. the "insufficient context" message that is produced without calling
    the LLM when retrieval finds nothing.
    """
    if not text:
        return 0.0
    persian = 0
    total = 0
    for char in text:
        if not char.isalpha():
            continue
        total += 1
        if "\u0600" <= char <= "\u06FF" or "\u0750" <= char <= "\u077F" or "\uFB50" <= char <= "\uFEFF":
            persian += 1
    return persian / total if total else 0.0

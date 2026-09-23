"""Shared date finders for DOB and electronic-signature extraction.

Supports numeric dates and dates that mix numbers with short/full month names.
"""

from __future__ import annotations

import re

_CLEAN = re.compile(r"\s+")

_MONTH = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)

# Capture group 1 is always the full date text.
_DATE = re.compile(
    r"\b("
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)?"
    r"|"
    r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
    r"|"
    rf"{_MONTH}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{2,4}}"
    r"|"
    rf"\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH}\.?,?\s+\d{{2,4}}"
    r"|"
    rf"\d{{1,2}}[/-]{_MONTH}[/-]\d{{2,4}}"
    r"|"
    rf"{_MONTH}[/-]\d{{1,2}}[/-]\d{{2,4}}"
    r")\b",
    flags=re.IGNORECASE,
)


def find_dates(text: str) -> list[re.Match[str]]:
    return list(_DATE.finditer(text or ""))


def normalize_date(raw: str) -> str:
    text = _CLEAN.sub(" ", (raw or "").strip()).rstrip(".,;")
    if not text:
        return ""
    iso = re.match(
        r"^(\d{4}-\d{2}-\d{2})(?:[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?)?$",
        text,
        flags=re.IGNORECASE,
    )
    if iso:
        return iso.group(1)
    return text

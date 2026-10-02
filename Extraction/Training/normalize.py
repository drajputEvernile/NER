"""Normalized value per field, so a reviewed value and an extracted candidate compare equal
when they differ only in formatting ('5/3/2024' == '05/03/2024', 'Burns, Lauren N MD' ==
'Lauren Burns')."""

from __future__ import annotations

import re

from Electronic_Signature.extract import is_credential
from Util.dates import canonical_date, find_date_ranges, find_dates

_NAME_FIELDS = frozenset({"name", "provider_name"})
# Words that sit between the name and the date of a signature line, never part of either.
_ESIG_FILLER = frozenset({
    "on", "at", "am", "pm", "by", "signed", "electronically", "esigned", "signature", "dated", "date", "time",
})
_CLOCK = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:am|pm)?\b", re.IGNORECASE)
_TITLES = frozenset({"mr", "mrs", "ms", "miss", "mx", "dr"})
_SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv"})
_RANGE_SPLIT = re.compile(r"\s+(?:-|–|to|thru|through)\s+", re.IGNORECASE)


def norm_date(value: str) -> str:
    text = (value or "").strip()
    return canonical_date(text) if text else ""


def norm_dos(value: str) -> str:
    """'from|to' ISO pair; a single date is its own range."""
    text = (value or "").strip()
    if not text:
        return ""
    ranges = find_date_ranges(text)
    if ranges:
        left, right = ranges[0].group(1), ranges[0].group(2)
    else:
        parts = _RANGE_SPLIT.split(text, maxsplit=1)
        left, right = parts[0], parts[-1]
    return f"{norm_date(left)}|{norm_date(right)}"


def norm_id(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def norm_name(value: str) -> str:
    """Order-free words of 2+ letters; titles, suffixes and credentials dropped."""
    words: list[str] = []
    for raw in re.split(r"[\s,;]+", value or ""):
        token = raw.strip(".()")
        if not token or is_credential(token):
            continue
        folded = re.sub(r"[^a-z'\-]", "", token.casefold()).strip("'-")
        if len(folded) < 2 or folded in _TITLES or folded in _SUFFIXES:
            continue
        words.append(folded)
    return " ".join(sorted(words))


def norm_esig(value: str) -> str:
    """'name words|ISO date' of a whole signature value ('Valerie Fuller, D.O. on 01/19/2024 at
    11:26 am' == 'Fuller, Valerie 1/19/24'); "" when it has neither."""
    text = value or ""
    dates = find_dates(text)
    date = norm_date(dates[0].group(1)) if dates else ""
    if dates:
        text = text[: dates[0].start()] + " " + text[dates[0].end():]
    text = _CLOCK.sub(" ", text)
    words = [word for word in re.split(r"[\s,;]+", text) if word.strip(".()").casefold() not in _ESIG_FILLER]
    name = norm_name(" ".join(words))
    return f"{name}|{date}" if name or date else ""


def norm_page(page_no: str, total: str = "") -> str:
    number = re.sub(r"\D", "", page_no or "")
    count = re.sub(r"\D", "", total or "")
    if not number:
        return ""
    return f"{int(number)}/{int(count)}" if count else str(int(number))


_PAGE_TEXT = re.compile(r"(\d{1,4})(?:\s*(?:/|of)\s*(\d{1,4}))?", re.IGNORECASE)
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def normalize_typed(field: str, value: str) -> str:
    """Normalize a value a reviewer typed ('Page 2 of 5' for Page No); "" when it isn't valid."""
    if field == "page_no":
        found = _PAGE_TEXT.search(value or "")
        return norm_page(found.group(1), found.group(2) or "") if found else ""
    norm = normalize(field, value)
    # canonical_date falls back to the raw text for OCR candidates; a typed date must parse.
    if field in {"dob", "dos"} and not all(_ISO_DATE.fullmatch(part) for part in norm.split("|")):
        return ""
    return norm


def norm_heading(value: str) -> str:
    """Words only: 'CHIEF COMPLAINT:' == 'Chief Complaint'."""
    return " ".join(re.findall(r"[a-z0-9]+", (value or "").casefold()))


def normalize(field: str, value: str, extra: str = "") -> str:
    """extra: page total for page_no; ignored elsewhere."""
    if field.startswith("heading_"):
        return norm_heading(value)
    if field == "dob":
        return norm_date(value)
    if field == "dos":
        return norm_dos(value)
    if field == "member_id":
        return norm_id(value)
    if field in _NAME_FIELDS:
        return norm_name(value)
    if field == "electronic_signature":
        return norm_esig(value)
    if field == "page_no":
        return norm_page(value, extra)
    return (value or "").strip().casefold()


def normalize_selected(field: str, value: str) -> str:
    """Normal form of words a reviewer selected on the page. The selection can be anything the
    OCR read, right or wrong, so it never fails: text the field's normalizer cannot parse
    keeps its lower-cased words, which still compares equal to the same selection."""
    norm = normalize(field, value)
    if field == "page_no":
        found = _PAGE_TEXT.search(value or "")
        norm = norm_page(found.group(1), found.group(2) or "") if found else ""
    return norm or " ".join(re.findall(r"[a-z0-9]+", (value or "").casefold()))

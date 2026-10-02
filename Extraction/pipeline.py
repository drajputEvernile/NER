"""Run both extraction models on a page.

Two separate models, each with its own inputs:

  KV_Extraction     reads the OCR JSON only (every word and its box): extract_page_kv. It never
                    opens the page image. Provider runs before Member Name so the member names
                    found above provider designation blocks are reused, not recomputed. DOB and
                    Name then get a copy of each accepted value at its other occurrences on the
                    page (Util/mentions.py).
  Heading_Detector  reads the OCR JSON and the page image (a layout model finds the headings on
                    the picture; the OCR words give each its text): extract_page_headings. It runs
                    after the key-value model on the same words and keys, because a heading
                    candidate knows whether its words are a KV key.

Everything either model finds is a candidate row (Training/features.py); the workbook (run.py)
and the trained versions (Training/model.py) read those rows.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import pandas as pd

from DOS.extract import extract_page as dos_page
from Electronic_Signature.extract import extract_page as esig_page
from Heading.extract import extract_page as heading_page
from Heading.extract import heading_fields
from Member_DOB.extract import extract_page as dob_page
from Member_ID.extract import extract_page as id_page
from Member_Name.extract import extract_page as name_page
from Page_No.extract import extract_page as page_no_page
from Provider_Name.extract import extract_provider_hits, role_member_hints
from Training.features import COLUMNS as CANDIDATE_COLUMNS
from Training.features import SHEETS, document_rows, field_value, kv_value_words
from Training.normalize import norm_heading
from Util.geometry import Word, page_size, words_from_page
from Util.keys import KeyHit, find_key_hits
from Util.mentions import REPEAT_FIELDS, repeat_mentions

# v0 = the hand-written rules pick the value; vNNN = a trained version (Training/model.py) picks.
MODEL_VERSION = "v0"


@dataclass(frozen=True)
class FieldSpec:
    id: str
    label: str
    sheet: str


FIELDS: list[FieldSpec] = [
    FieldSpec("dob", "Member DOB", SHEETS["dob"]),
    FieldSpec("member_id", "Member ID", SHEETS["member_id"]),
    FieldSpec("name", "Member Name", SHEETS["name"]),
    FieldSpec("provider_name", "Provider Name", SHEETS["provider_name"]),
    FieldSpec("electronic_signature", "E-Signature", SHEETS["electronic_signature"]),
    FieldSpec("dos", "Date of Service", SHEETS["dos"]),
    FieldSpec("page_no", "Page No", SHEETS["page_no"]),
]


@dataclass
class PageResult:
    page: dict
    page_w: float
    page_h: float
    hits: list[KeyHit]
    words: list[Word] = field(default_factory=list)
    rows: dict[str, list] = field(default_factory=dict)


def extract_page_kv(page: dict) -> PageResult:
    """Every key-value field for one page from a single word parse and key search (OCR JSON only)."""
    words = words_from_page(page)
    page_w, page_h = page_size(page, words)
    hits = find_key_hits(words, page_w, page_h)
    provider = extract_provider_hits(hits, words, page_w, page_h)
    rows = {
        "dob": dob_page(hits, words, page_w, page_h),
        "member_id": id_page(hits, words),
        "name": name_page(hits, words, page_w, page_h, role_hints=role_member_hints(provider)),
        "provider_name": provider,
        "electronic_signature": esig_page(hits),
        "dos": dos_page(hits, words, page_h),
        "page_no": page_no_page(words, page_h),
    }
    for name in REPEAT_FIELDS:
        rows[name] = rows[name] + repeat_mentions(name, rows[name], words, page_h)
    return PageResult(page=page, page_w=page_w, page_h=page_h, hits=hits, words=words, rows=rows)


# A heading made mostly of words the key-value model read (the patient name in the page header,
# "DOB 12/17/1965", "Page 3 of 5") is the page's data, not a section heading.
KV_WORDS_SHARE = 0.6


def _kv_words(result: PageResult) -> set[int]:
    """Every word of every key and extracted value the key-value model found on the page."""
    taken: set[int] = set()
    for hit in result.hits:
        if hit.trusted:
            taken.update(hit.word_indexes)
    for name, rows in result.rows.items():
        for row in rows:
            if not getattr(row, "accepted", False):
                continue
            value = field_value(name, row)[0]
            taken.update(word.index for word in kv_value_words(name, row, value, result.words))
            key_hit = getattr(row, "key_hit", None)
            if key_hit is not None:
                taken.update(key_hit.word_indexes)
    return taken


def extract_page_headings(record_id: str, result: PageResult) -> None:
    """Heading candidates of every configured detector, added to the page's rows (needs the page image)."""
    kv_words = _kv_words(result)
    found = heading_page(record_id, result.page, result.words, result.hits, result.page_w, result.page_h)
    for rows in found.values():
        for row in rows:
            indexes = {word.index for word in row.words}
            share = len(indexes & kv_words) / len(indexes) if indexes else 0.0
            row.value_overlap = max(row.value_overlap, round(share, 3))
            if row.accepted and share >= KV_WORDS_SHARE:
                row.accepted, row.note = False, "KV key / value"
    result.rows.update(found)


def _mark_running_headers(pages: list[PageResult]) -> None:
    """A page header that is the same text on more than one page of a document (a fax line, a
    letterhead) is a running header, not a heading of any page's content."""
    if len(pages) < 2:
        return
    for name in heading_fields():
        seen: dict[str, set[int]] = {}
        for number, page in enumerate(pages):
            for row in page.rows.get(name, []):
                if row.kind == "page_header" and row.accepted:
                    seen.setdefault(norm_heading(row.text), set()).add(number)
        for page in pages:
            for row in page.rows.get(name, []):
                if row.kind == "page_header" and row.accepted and len(seen.get(norm_heading(row.text), ())) >= 2:
                    row.accepted, row.note = False, "running header"


@dataclass
class DocumentResult:
    record_id: str
    pages: list[PageResult]
    time_seconds: float

    def candidates(self, run_id: str, model_version: str = MODEL_VERSION) -> pd.DataFrame:
        """Candidate log: every field's candidates on every page, with features."""
        # Heading fields only when their detector ran, so a skipped detector is not logged as "no headings".
        ran = [name for name in heading_fields() if any(name in page.rows for page in self.pages)]
        rows = document_rows(self, run_id, model_version, [spec.id for spec in FIELDS] + ran)
        return pd.DataFrame(rows, columns=CANDIDATE_COLUMNS)


def extract_document(document, *, headings: bool = True) -> DocumentResult:
    started = time.perf_counter()
    pages = [extract_page_kv(page) for page in document.pages]
    if headings:
        for result in pages:
            extract_page_headings(document.record_id, result)
        _mark_running_headers(pages)
    return DocumentResult(
        record_id=document.record_id,
        pages=pages,
        time_seconds=round(time.perf_counter() - started, 3),
    )

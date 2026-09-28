"""Run every KV extractor on a page in one pass.

Each page is parsed once and its catalog keys are found once; every field reads the
same key list. Provider runs before Member Name so the member names found above
provider designation blocks are reused, not recomputed. DOB and Name then get
a copy of each accepted value at its other occurrences on the page (Util/mentions.py).
Overlays for every field
are drawn from the same results with the page image opened once.

Headings run after the KV fields on the same words and keys (a heading candidate knows
whether its words are a KV key); each layout detector is its own field (Heading/extract.py).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

import pandas as pd

from DOS import output as dos_output
from DOS.extract import extract_page as dos_page
from Electronic_Signature import output as esig_output
from Electronic_Signature.extract import extract_page as esig_page
from Heading.extract import extract_page as heading_page
from Heading.extract import heading_fields
from Member_DOB import output as dob_output
from Member_DOB.extract import extract_page as dob_page
from Member_ID import output as id_output
from Member_ID.extract import extract_page as id_page
from Member_Name import output as name_output
from Member_Name.extract import extract_page as name_page
from Page_No import output as page_no_output
from Page_No.extract import extract_page as page_no_page
from Provider_Name import output as provider_output
from Provider_Name.extract import extract_provider_hits, role_member_hints
from Training.features import COLUMNS as CANDIDATE_COLUMNS
from Training.features import document_rows
from Util.geometry import Word, page_size, words_from_page
from Util.keys import KeyHit, find_key_hits
from Util.mentions import REPEAT_FIELDS, repeat_mentions
from Util.overlay import render_heading_overlays, render_page_overlays

# v0 = the hand-written rules pick the value; vNNN = a trained version (Training/model.py) picks.
MODEL_VERSION = "v0"


@dataclass(frozen=True)
class FieldSpec:
    id: str
    label: str
    folder_prefix: str
    excel_prefix: str
    detail_csv: str
    output: ModuleType  # COLUMNS, to_row, SUMMARY_COLUMNS, summarize


FIELDS: list[FieldSpec] = [
    FieldSpec("dob", "Member DOB", "DOB", "dob", "extraction_dob.csv", dob_output),
    FieldSpec("member_id", "Member ID", "ID", "ID", "extraction_id.csv", id_output),
    FieldSpec("name", "Member Name", "Name", "MName", "extraction_name.csv", name_output),
    FieldSpec("provider_name", "Provider Name", "Provider", "PName", "extraction_provider.csv", provider_output),
    FieldSpec(
        "electronic_signature", "Electronic Signature", "ESig", "ESig", "extraction_esig.csv", esig_output
    ),
    FieldSpec("dos", "DOS", "DOS", "DOS", "extraction_dos.csv", dos_output),
    FieldSpec("page_no", "Page No", "PageNo", "PageNo", "extraction_page.csv", page_no_output),
]


@dataclass
class PageResult:
    page: dict
    page_w: float
    page_h: float
    hits: list[KeyHit]
    words: list[Word] = field(default_factory=list)
    rows: dict[str, list] = field(default_factory=dict)


def extract_page_all(page: dict) -> PageResult:
    """Every field for one page from a single word parse and key search."""
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


@dataclass
class DocumentResult:
    record_id: str
    pages: list[PageResult]
    time_seconds: float

    def frames(self, spec: FieldSpec) -> tuple[pd.DataFrame, pd.DataFrame]:
        """(detail rows, one-row record summary) for one field."""
        output = spec.output
        detail = pd.DataFrame(
            [
                output.to_row(self.record_id, result.page, hit)
                for result in self.pages
                for hit in result.rows[spec.id]
            ],
            columns=output.COLUMNS,
        )
        summary = pd.DataFrame(
            [
                {
                    "RecordId": self.record_id,
                    "PageCount": str(len(self.pages)),
                    **output.summarize([result.rows[spec.id] for result in self.pages]),
                    "TimeSeconds": f"{self.time_seconds:.3f}",
                }
            ],
            columns=output.SUMMARY_COLUMNS,
        )
        return detail, summary

    def candidates(self, run_id: str, model_version: str = MODEL_VERSION) -> pd.DataFrame:
        """Candidate log: every field's candidates on every page, with features."""
        # Heading fields only when their detector ran, so a skipped detector is not logged as "no headings".
        ran = [name for name in heading_fields() if any(name in page.rows for page in self.pages)]
        rows = document_rows(self, run_id, model_version, [spec.id for spec in FIELDS] + ran)
        return pd.DataFrame(rows, columns=CANDIDATE_COLUMNS)


def extract_document(document, *, headings: bool = True) -> DocumentResult:
    started = time.perf_counter()
    pages = [extract_page_all(page) for page in document.pages]
    if headings:
        for result in pages:
            result.rows.update(
                heading_page(document.record_id, result.page, result.words, result.hits, result.page_w, result.page_h)
            )
    return DocumentResult(
        record_id=document.record_id,
        pages=pages,
        time_seconds=round(time.perf_counter() - started, 3),
    )


def apply_model_selection(result: DocumentResult, log: pd.DataFrame) -> None:
    """Push a trained version's choices (model_accepted / model_selected / model_level) into
    the rule rows, so detail CSVs, the workbook, record summaries and overlays show what the
    model extracted and picked.

    The candidate log lists each page-field's rows in the same order as result.rows. A value
    the model selects is also marked accepted (outputs need both); the rules' own decision is
    kept in the log as rule_accepted / rule_selected.
    """
    headings = set(heading_fields())
    rows = log[~log["is_placeholder"].astype(str).isin({"1", "True", "true"})]
    for page in result.pages:
        number = str(page.page.get("pageNumber") or "")
        name = str(page.page.get("fileName") or "")
        mine = rows[(rows["page_number"].astype(str) == number) & (rows["file_name"].astype(str) == name)]
        for field_id, part in mine.groupby("field", sort=False):
            hits = page.rows.get(field_id) or []
            if len(hits) != len(part):
                raise RuntimeError(f"{result.record_id} p{number} {field_id}: {len(hits)} rows vs {len(part)} logged")
            columns = (part[name].astype(str) for name in ("model_accepted", "model_selected", "model_level"))
            for hit, accepted, chosen, level in zip(hits, *columns):
                if not chosen:
                    continue
                if field_id in headings:
                    hit.accepted = chosen == "1"
                    if level:
                        hit.level = level
                else:
                    hit.selected = chosen == "1"
                    hit.accepted = accepted == "1" or hit.selected


def render_document_overlays(
    result: DocumentResult,
    field_dirs: dict[str, Path],
    overall_dir: Path | None,
    heading_dirs: dict[str, Path] | None = None,
) -> int:
    """Per-field + overall (+ per heading detector) overlays for every page, from the extraction results."""
    written = 0
    for page in result.pages:
        written += render_page_overlays(
            result.record_id,
            page.page,
            page.hits,
            page.rows["page_no"],
            page.page_w,
            page.page_h,
            field_dirs,
            overall_dir,
        )
        dirs = {name: path for name, path in (heading_dirs or {}).items() if name in page.rows}
        written += render_heading_overlays(result.record_id, page.page, page.rows, page.page_w, page.page_h, dirs)
    return written

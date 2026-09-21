"""Run Member DOB extraction only.

Reads OCR JSON from Util.config.OCR_Input and raw images from Util.config.Raw_Input.
Writes under Data/KV_Output/DOB_Extraction_{timestamp}/:
  extraction_dob.csv
  extraction_summary.csv
  overlays/{RecordId}/page_{NN}_{fileName}

Usage (from repo root):
  .\\.venv\\Scripts\\python.exe KV_Extraction\\run_dob_extraction.py
"""

from __future__ import annotations

import json
import logging
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import pandas as pd

from Member_DOB.extract import DobHit, extract_box, mark_selected
from Util import config
from Util.geometry import page_size, words_from_page
from Util.keys import find_key_hits
from Util.model import get_model
from Util.overlay import save_overlays

logger = logging.getLogger(__name__)

COLUMNS = [
    "RecordId",
    "FileName",
    "PageNumber",
    "Key",
    "Region",
    "Sentence",
    "Ner_Text",
    "Value",
    "Score",
    "Accepted",
    "Selected",
    "Source",
    "Accuracy",
]


@dataclass
class Document:
    record_id: str
    pages: list[dict]
    source: str


def _load_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("skip %s (%s)", path, exc)
        return None
    return data if isinstance(data, dict) else None


def load_local_documents(root: Path) -> list[Document]:
    if not root.is_dir():
        raise SystemExit(f"OCR_Input is not a folder: {root}")
    documents: list[Document] = []
    for child in sorted(path for path in root.iterdir() if path.is_dir()):
        jsons = sorted(path for path in child.glob("*.json") if path.is_file())
        if not jsons:
            continue
        path = max(jsons, key=lambda item: item.stat().st_size)
        data = _load_json(path)
        if not data:
            continue
        pages = data.get("pages")
        if not isinstance(pages, list) or not pages:
            logger.info("skip %s (no pages)", child.name)
            continue
        documents.append(Document(record_id=child.name, pages=pages, source=str(path)))
    return documents


def _score(hit: DobHit) -> str:
    if not hit.accepted and not hit.ner_text:
        return ""
    return f"{hit.score:.4f}"


def _row(record_id: str, page: dict, hit: DobHit) -> dict[str, str]:
    return {
        "RecordId": record_id,
        "FileName": str(page.get("fileName") or ""),
        "PageNumber": str(page.get("pageNumber") or ""),
        "Key": hit.key,
        "Region": hit.region,
        "Sentence": hit.sentence,
        "Ner_Text": hit.ner_text,
        "Value": hit.value,
        "Score": _score(hit),
        "Accepted": "yes" if hit.accepted else "no",
        "Selected": "yes" if hit.selected else "no",
        "Source": hit.source,
        "Accuracy": "",
    }


def _record_dob(rows: list[DobHit]) -> str:
    present = [row for row in rows if row.accepted and row.selected and row.value]
    if not present:
        return ""
    counts = Counter(row.value.casefold() for row in present)
    best_count = max(counts.values())
    tied = {text for text, count in counts.items() if count == best_count}
    winners = [row for row in present if row.value.casefold() in tied]
    winners.sort(key=lambda row: row.score, reverse=True)
    return winners[0].value


def extract_dob(documents: list[Document]) -> tuple[pd.DataFrame, pd.DataFrame]:
    get_model()
    dob_rows: list[dict[str, str]] = []
    summary_rows: list[dict[str, str]] = []
    for document in documents:
        started = time.perf_counter()
        logger.info("extract %s pages=%s source=%s", document.record_id, len(document.pages), document.source)
        chosen: list[DobHit] = []
        for page in document.pages:
            words = words_from_page(page)
            page_w, page_h = page_size(page, words)
            hits = find_key_hits(words, page_w, page_h)
            dobs = [extract_box(hit) for hit in hits if hit.trusted and hit.field == "dob" and hit.value_text]
            mark_selected(dobs)
            chosen.extend(dobs)
            dob_rows.extend(_row(document.record_id, page, row) for row in dobs)
        elapsed = round(time.perf_counter() - started, 3)
        summary_rows.append(
            {
                "RecordId": document.record_id,
                "PageCount": str(len(document.pages)),
                "DOB": _record_dob(chosen),
                "TimeSeconds": f"{elapsed:.3f}",
            }
        )
    dob_frame = pd.DataFrame(dob_rows, columns=COLUMNS)
    summary = pd.DataFrame(summary_rows, columns=["RecordId", "PageCount", "DOB", "TimeSeconds"])
    return dob_frame, summary


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    documents = load_local_documents(config.OCR_Input)
    if not documents:
        raise SystemExit(f"No OCR JSON under {config.OCR_Input}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = config.Local_Output / f"DOB_Extraction_{stamp}"
    folder.mkdir(parents=True, exist_ok=True)

    dob_frame, summary = extract_dob(documents)
    dob_frame.to_csv(folder / "extraction_dob.csv", index=False)
    summary.to_csv(folder / "extraction_summary.csv", index=False)
    save_overlays(documents, folder, field="dob")
    logger.info("wrote %s", folder)
    print(folder)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

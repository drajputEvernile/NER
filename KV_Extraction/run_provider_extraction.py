"""Run Provider Name extraction.

A provider is accepted only when a person name is followed by a known
credential suffix (MD, DO, PA-C, ...). Multiple suffixes may be comma/period
separated.

Usage (from repo root):
  .\\.venv\\Scripts\\python.exe KV_Extraction\\run_provider_extraction.py
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

from Provider_Name.extract import ProviderHit, extract_box, mark_selected
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
    "Scale",
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


def _score(hit: ProviderHit) -> str:
    if not hit.accepted and not hit.ner_text:
        return ""
    return f"{hit.score:.4f}"


def _row(record_id: str, page: dict, hit: ProviderHit) -> dict[str, str]:
    return {
        "RecordId": record_id,
        "FileName": str(page.get("fileName") or ""),
        "PageNumber": str(page.get("pageNumber") or ""),
        "Key": hit.key,
        "Region": hit.region,
        "Scale": hit.scale,
        "Sentence": hit.sentence,
        "Ner_Text": hit.ner_text,
        "Value": hit.value,
        "Score": _score(hit),
        "Accepted": "yes" if hit.accepted else "no",
        "Selected": "yes" if hit.selected else "no",
        "Source": hit.source if hit.accepted or hit.ner_text else "",
        "Accuracy": "",
    }


def _record_provider(rows: list[ProviderHit]) -> str:
    present = [row for row in rows if row.accepted and row.selected and row.value]
    if not present:
        return ""
    counts = Counter(row.value.casefold() for row in present)
    best_count = max(counts.values())
    tied = {text for text, count in counts.items() if count == best_count}
    winners = [row for row in present if row.value.casefold() in tied]
    winners.sort(key=lambda row: row.score, reverse=True)
    return winners[0].value


def extract_provider(documents: list[Document]) -> tuple[pd.DataFrame, pd.DataFrame]:
    get_model()
    provider_rows: list[dict[str, str]] = []
    summary_rows: list[dict[str, str]] = []
    for document in documents:
        started = time.perf_counter()
        logger.info("extract %s pages=%s source=%s", document.record_id, len(document.pages), document.source)
        chosen: list[ProviderHit] = []
        for page in document.pages:
            words = words_from_page(page)
            page_w, page_h = page_size(page, words)
            hits = find_key_hits(words, page_w, page_h)
            providers = [
                extract_box(hit)
                for hit in hits
                if hit.trusted and hit.field == "provider_name" and hit.value_text
            ]
            mark_selected(providers)
            chosen.extend(providers)
            provider_rows.extend(_row(document.record_id, page, row) for row in providers)
        elapsed = round(time.perf_counter() - started, 3)
        summary_rows.append(
            {
                "RecordId": document.record_id,
                "PageCount": str(len(document.pages)),
                "ProviderName": _record_provider(chosen),
                "TimeSeconds": f"{elapsed:.3f}",
            }
        )
    provider_frame = pd.DataFrame(provider_rows, columns=COLUMNS)
    summary = pd.DataFrame(summary_rows, columns=["RecordId", "PageCount", "ProviderName", "TimeSeconds"])
    return provider_frame, summary


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    documents = load_local_documents(config.OCR_Input)
    if not documents:
        raise SystemExit(f"No OCR JSON under {config.OCR_Input}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = config.Local_Output / f"Provider_Extraction_{stamp}"
    folder.mkdir(parents=True, exist_ok=True)

    provider_frame, summary = extract_provider(documents)
    provider_frame.to_csv(folder / "extraction_provider.csv", index=False)
    summary.to_csv(folder / "extraction_summary.csv", index=False)
    save_overlays(documents, folder, field="provider_name")
    logger.info("wrote %s", folder)
    print(folder)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Run both extraction models on every document into one Excel workbook, with a resumable queue.

The queue is built from the record folders under Raw_Input (each holding that record's page images);
every one is looked up in OCR_Input, and the run starts on the records that have OCR JSON. A record
with no usable OCR is listed in the queue (no_ocr) and in the log, never silently dropped.

Each page is parsed once and its catalog keys are found once. The KV_Extraction model (DOB,
Member ID, Member Name, Provider Name, E-signature, DOS, Page No) reads the OCR JSON only. The
Heading_Detector model runs after it, on the OCR JSON and the page image, for each layout
detector in config.Heading_Models (see pipeline.py). Documents are the unit of work in the
queue; a resumed run skips completed documents and redoes the rest.

Usage (from repo root or this folder):
  .\\.venv\\Scripts\\python.exe Extraction\\run.py
  .\\.venv\\Scripts\\python.exe Extraction\\run.py --fresh
  .\\.venv\\Scripts\\python.exe Extraction\\run.py -N 7
  .\\.venv\\Scripts\\python.exe Extraction\\run.py -N 5 10
  .\\.venv\\Scripts\\python.exe Extraction\\run.py -M 5 -N 10
  .\\.venv\\Scripts\\python.exe Extraction\\run.py -N 0
  .\\.venv\\Scripts\\python.exe Extraction\\run.py --fresh --records-from KV_Run_20260927_130019 --model-version v003

Queue file (active run):
  {Run_Output}/kv_run_queue.json

Output (everything a run saves; nothing else is written):
  {Run_Output}/KV_Run_{run_id}/run.json            (run summary, kept in sync with the queue)
  {Run_Output}/KV_Run_{run_id}/KV_Extraction.xlsx  sheets Member_Name, Member_ID, Member_DOB,
      Provider_Name, E_Sign, DOS, Page_No, Headings (one row per candidate: what it is, the
      sentence it was read from with every word's position, its features) and Overall (the value
      selected for each field on each page). The Review UI writes the reviews into the same
      workbook, and training reads it back: it is the only file that has to move between machines.

-N / --max-pages  [M] N   and   -M / --min-pages M:
  -N N    = documents with N pages or fewer (0 = no upper limit, every document)
  -N M N  = documents with more than M and at most N pages: -N 5 10 runs 6..10 pages
  -M M    = the same lower limit on its own (-M 5 -N 10 = -N 5 10; -M 5 alone = more than 5)

--records-from RUN:
  only the documents of an earlier run (a rerun of that batch)
--model-version V:
  extraction model version from the registries (Training/registry.py); v0 = rules. Default:
  config.Default_Model_Version (v003). A trained
  version loads its KV_Extraction model and its Heading_Detector model, whichever exist.

Paths: config.py holds every absolute path (Raw_Input, OCR_Input, Output_Root and the model folders).
Run_Output, Training_Data and Rerun_Logs are created under Output_Root on the first run.

Models: before anything runs, GLiNER and the heading detector are checked at config.Ner_Model_Path and config.Heading_Models
and downloaded from Hugging Face (pinned revisions, config.Model_Sources) when missing. A trained
version (config.Extraction_Models/KV_vNNN and Heading_vNNN) is built locally
and must be copied with the models folder.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import pandas as pd

from Util import config
from Util.model import get_model
from Util.model_setup import ensure_models
from Util.workbook import read_workbook, write_workbook

import pipeline
from pipeline import FIELDS
from Heading.extract import DETECTORS, heading_fields, load_detectors
from Training import registry
from Training.features import (
    HEADING_SHEET,
    OVERALL_COLUMNS,
    OVERALL_SHEET,
    SHEETS,
    catalog_hash,
    log_to_sheet,
    sheet_columns,
)
from Training.model import apply_models
from Util.documents import MissingOcr, load_documents

logger = logging.getLogger(__name__)

QUEUE_NAME = "kv_run_queue.json"
# Queues written by an earlier layout of the outputs (per-field CSVs, overlays, candidate folders).
QUEUE_VERSION = "workbook_v1"
# The workbook is rewritten (all sheets, atomically) when this many seconds have passed since the
# last write and when the run ends, so a long run does not rewrite it after every document. A
# document counts as completed only once a written workbook holds it.
CHECKPOINT_SECONDS = 60.0

# Sheet order of the workbook.
SHEET_ORDER = [*SHEETS.values(), HEADING_SHEET, OVERALL_SHEET]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _queue_path() -> Path:
    return config.Run_Output / QUEUE_NAME


def _run_folder(run_id: str) -> Path:
    return config.Run_Output / f"KV_Run_{run_id}"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _load_queue(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("queue unreadable (%s)", exc)
        return None
    return data if isinstance(data, dict) else None


def _page_count(documents: list) -> int:
    return sum(len(document.pages) for document in documents)


def _recompute_progress(queue: dict[str, Any]) -> None:
    done = [doc for doc in queue["documents"] if doc["status"] == "completed"]
    completed_docs = len(done)
    completed_pages = sum(int(doc["page_count"]) for doc in done)
    queue["completed_documents"] = completed_docs
    queue["completed_pages"] = completed_pages

    started = queue.get("start_time")
    elapsed = float(queue.get("total_time_seconds") or 0)
    if started:
        try:
            elapsed = max(0.0, (datetime.now(timezone.utc) - datetime.fromisoformat(started)).total_seconds())
        except ValueError:
            pass
    queue["total_time_seconds"] = round(elapsed, 3)
    queue["avg_time_per_doc"] = round(elapsed / completed_docs, 3) if completed_docs else None
    queue["avg_time_per_page"] = round(elapsed / completed_pages, 3) if completed_pages else None


def _save_queue(queue: dict[str, Any]) -> None:
    _recompute_progress(queue)
    queue["pid"] = os.getpid()
    _atomic_write_json(_queue_path(), queue)
    # The queue file is archived when the run ends; run.json stays with the run's outputs.
    _atomic_write_json(Path(queue["run_folder"]) / "run.json", queue)


def _source_records(run_name: str) -> list[str]:
    folder = Path(run_name)
    if not folder.is_absolute():
        folder = config.Run_Output / run_name
    summary = _load_queue(folder / "run.json") or {}
    records = [doc["record_id"] for doc in summary.get("documents") or [] if doc.get("record_id")]
    if not records:
        workbook = folder / config.Workbook_Name
        if workbook.is_file():
            records = sorted(set(read_workbook(workbook)[OVERALL_SHEET]["RecordId"]))
    if not records:
        raise SystemExit(f"--records-from: no documents found in {folder}")
    return records


def _probe_ner_model() -> tuple[str, str, bool, str | None]:
    model_path = Path(config.Ner_Model_Path)
    name = model_path.name
    try:
        get_model()
        return name, str(model_path), True, None
    except Exception as exc:  # noqa: BLE001 - surface load failure in queue
        return name, str(model_path), False, str(exc)


def _heading_meta() -> dict[str, Any]:
    errors = load_detectors()
    return {
        name: {
            "id": name,
            "label": DETECTORS[name].label,
            "model_path": str(config.Heading_Models[name]),
            "loaded": errors[name] is None,
            "error": errors[name],
        }
        for name in heading_fields()
    }


def _in_page_range(page_count: int, min_pages: int, max_pages: int) -> bool:
    """min_pages < page_count <= max_pages; max_pages <= 0 means no upper limit."""
    return page_count > min_pages and (max_pages <= 0 or page_count <= max_pages)


def _page_range_text(min_pages: int, max_pages: int) -> str:
    parts = ([f"> {min_pages}"] if min_pages > 0 else []) + ([f"<= {max_pages}"] if max_pages > 0 else [])
    return f"pages {' and '.join(parts)}" if parts else "any page count"


def _filter_by_pages(documents: list, min_pages: int, max_pages: int) -> tuple[list, list]:
    """Keep docs with min_pages < page_count <= max_pages (see _in_page_range)."""
    kept = [document for document in documents if _in_page_range(len(document.pages), min_pages, max_pages)]
    skipped = [document for document in documents if not _in_page_range(len(document.pages), min_pages, max_pages)]
    return kept, skipped


def _new_queue(
    documents: list,
    run_id: str,
    *,
    min_pages: int = 0,
    max_pages: int = 0,
    model_version: str = config.Default_Model_Version,
    source_run: str | None = None,
    raw_records: int = 0,
    no_ocr: list[MissingOcr] | None = None,
) -> dict[str, Any]:
    ner_name, ner_path, ner_ok, ner_error = _probe_ner_model()
    run_folder = _run_folder(run_id)
    return {
        "version": QUEUE_VERSION,
        "run_id": run_id,
        "status": "running",
        "source_run": source_run,
        "min_pages": int(min_pages),
        "max_pages": int(max_pages),
        "output_root": str(config.Run_Output),
        "run_folder": str(run_folder),
        "workbook_path": str(run_folder / config.Workbook_Name),
        "model_version": model_version,
        "catalog_hash": catalog_hash(),
        "ner_model_name": ner_name if ner_ok else None,
        "ner_model_path": ner_path,
        "ner_model_loaded": ner_ok,
        "ner_model_error": ner_error,
        "raw_input": str(config.Raw_Input),
        "ocr_input": str(config.OCR_Input),
        "raw_records": raw_records,
        # Raw records the OCR folder has nothing usable for: not in the queue's documents, kept here.
        "no_ocr": [
            {"record_id": item.record_id, "page_count": item.image_count, "reason": item.reason} for item in no_ocr or []
        ],
        "total_docs": len(documents),
        "total_pages": _page_count(documents),
        "completed_documents": 0,
        "completed_pages": 0,
        "start_time": _utc_now(),
        "end_time": None,
        "stop_time": None,
        "total_time_seconds": 0.0,
        "avg_time_per_page": None,
        "avg_time_per_doc": None,
        "fields": {spec.id: {"id": spec.id, "label": spec.label, "sheet": spec.sheet} for spec in FIELDS},
        "headings": _heading_meta(),
        "documents": [
            {
                "record_id": document.record_id,
                "page_count": len(document.pages),
                "status": "pending",
                "time_seconds": None,
                "error": None,
            }
            for document in documents
        ],
    }


# ---------------------------------------------------------------- the workbook


def _empty_sheets() -> dict[str, list[pd.DataFrame]]:
    return {name: [] for name in SHEET_ORDER}


def _selected_values(frame: pd.DataFrame, record_id: str, file_name: str, page_number: str) -> str:
    """The selected value(s) of one field on one page, in page order, each distinct value once."""
    if frame.empty:
        return ""
    rows = frame[
        (frame["record_id"] == record_id)
        & (frame["file_name"] == file_name)
        & (frame["page_number"] == page_number)
        & (frame["selected"].astype(str) == "1")
        & (frame["is_placeholder"].astype(str) != "1")
        & (frame["value"].astype(str) != "")
    ]
    seen: dict[str, str] = {}
    for value, norm in zip(rows["value"], rows["value_norm"]):
        seen.setdefault(str(norm) or str(value).casefold(), str(value))
    return " | ".join(seen.values())


def _document_sheets(queue: dict[str, Any], result: pipeline.DocumentResult, model_version: str) -> dict[str, pd.DataFrame]:
    """One document's rows for every sheet: a row per candidate, and Overall's selected values.

    With a trained version the candidate log is scored first and its choices become the accepted /
    selected columns the sheets and Overall are written from (the rules' own stay in rule_*).
    """
    log = result.candidates(queue["run_id"], model_version)
    log = apply_models(log, model_version)
    sheets: dict[str, pd.DataFrame] = {}
    for spec in FIELDS:
        sheets[spec.sheet] = log_to_sheet(log[log["field"] == spec.id], heading=False)
    sheets[HEADING_SHEET] = log_to_sheet(log[log["field"].isin(list(DETECTORS))], heading=True)

    page_count = str(len(result.pages))
    overall: list[dict[str, str]] = []
    for page in result.pages:
        record_id = result.record_id
        file_name = str(page.page.get("fileName") or "")
        page_number = str(page.page.get("pageNumber") or "")
        row = {"RecordId": record_id, "PageCount": page_count, "FileName": file_name, "PageNumber": page_number}
        for spec in FIELDS:
            row[spec.sheet] = _selected_values(sheets[spec.sheet], record_id, file_name, page_number)
        row[HEADING_SHEET] = _selected_values(sheets[HEADING_SHEET], record_id, file_name, page_number)
        overall.append(row)
    sheets[OVERALL_SHEET] = pd.DataFrame(overall, columns=OVERALL_COLUMNS)
    return sheets


def _workbook_frames(rows: dict[str, list[pd.DataFrame]]) -> dict[str, pd.DataFrame]:
    columns = {
        **{SHEETS[spec.id]: sheet_columns(False) for spec in FIELDS},
        HEADING_SHEET: sheet_columns(True),
        OVERALL_SHEET: OVERALL_COLUMNS,
    }
    frames: dict[str, pd.DataFrame] = {}
    for name in SHEET_ORDER:
        parts = [part for part in rows[name] if not part.empty]
        frames[name] = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=columns[name])
    return frames


def _write_workbook(queue: dict[str, Any], rows: dict[str, list[pd.DataFrame]]) -> Path:
    path = write_workbook(Path(queue["workbook_path"]), _workbook_frames(rows))
    logger.info("wrote workbook %s", path)
    return path


def _resume_rows(queue: dict[str, Any]) -> dict[str, list[pd.DataFrame]]:
    """The completed documents' rows from the workbook a stopped run left. A document the queue
    does not list as completed is dropped: its rows came from a write the queue never recorded."""
    rows = _empty_sheets()
    path = Path(queue["workbook_path"])
    if not path.is_file():
        return rows
    done = {doc["record_id"] for doc in queue["documents"] if doc["status"] == "completed"}
    sheets = read_workbook(path)
    for name in SHEET_ORDER:
        frame = sheets.get(name)
        if frame is None or frame.empty:
            continue
        column = "RecordId" if name == OVERALL_SHEET else "record_id"
        rows[name].append(frame[frame[column].isin(done)])
    return rows


def _archive_queue(queue: dict[str, Any], suffix: str = "") -> None:
    path = _queue_path()
    if not path.is_file():
        return
    archive = config.Run_Output / f"kv_run_queue_{queue.get('run_id', 'unknown')}{suffix}.json"
    path.replace(archive)
    logger.info("archived queue -> %s", archive)


def run_all(
    *,
    fresh: bool = False,
    min_pages: int = 0,
    max_pages: int = 0,
    records_from: str | None = None,
    model_version: str = config.Default_Model_Version,
) -> int:
    if not registry.is_runnable(model_version):
        raise SystemExit(f"Extraction model version {model_version!r} cannot run yet")
    config.make_output_folders()
    all_documents, no_ocr, raw = load_documents(config.Raw_Input, config.OCR_Input)
    if not raw:
        raise SystemExit(f"No record folders with page images under {config.Raw_Input}")
    logger.info(
        "Raw_Input: %s records; OCR found for %s, none usable for %s",
        len(raw),
        len(all_documents),
        len(no_ocr),
    )
    for item in no_ocr:
        logger.warning("no OCR for %s (%s page images): %s", item.record_id, item.image_count, item.reason)
    if not all_documents:
        raise SystemExit(f"None of the {len(raw)} records under {config.Raw_Input} has OCR under {config.OCR_Input}")
    if records_from:
        wanted = set(_source_records(records_from))
        all_documents = [document for document in all_documents if document.record_id in wanted]
        no_ocr = [item for item in no_ocr if item.record_id in wanted]
        if not all_documents:
            raise SystemExit(f"None of the documents of {records_from} have OCR under {config.OCR_Input}")

    page_range = _page_range_text(min_pages, max_pages)
    documents, skipped = _filter_by_pages(all_documents, min_pages, max_pages)
    no_ocr = [item for item in no_ocr if _in_page_range(item.image_count, min_pages, max_pages)]
    for document in skipped:
        logger.info("skip %s pages=%s (%s)", document.record_id, len(document.pages), page_range)
    if skipped:
        logger.info(
            "%s kept %s/%s documents (%s skipped)",
            page_range,
            len(documents),
            len(all_documents),
            len(skipped),
        )
    if not documents:
        raise SystemExit(
            f"No documents left after the page filter ({page_range}) "
            f"(found OCR for {len(all_documents)} of the {len(raw)} records under {config.Raw_Input})"
        )

    queue_path = _queue_path()
    queue = None if fresh else _load_queue(queue_path)
    if queue and queue.get("version") != QUEUE_VERSION:
        logger.info("queue %s is from an earlier output layout; starting a new run", queue.get("run_id"))
        _archive_queue(queue, "_legacy")
        queue = None
    if queue and queue.get("status") == "completed":
        logger.info("previous queue completed; starting a new run")
        _archive_queue(queue)
        queue = None

    rows = _empty_sheets()
    if queue is None:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        queue = _new_queue(
            documents,
            run_id,
            min_pages=min_pages,
            max_pages=max_pages,
            model_version=model_version,
            source_run=Path(records_from).name if records_from else None,
            raw_records=len(raw),
            no_ocr=no_ocr,
        )
        _save_queue(queue)
        logger.info(
            "started run %s %s (%s docs / %s pages; %s records without OCR left out)",
            run_id,
            page_range,
            queue["total_docs"],
            queue["total_pages"],
            len(queue["no_ocr"]),
        )
    else:
        logger.info("resuming run %s from %s", queue.get("run_id"), queue_path)
        queue["status"] = "running"
        queue["stop_time"] = None
        if not queue.get("ner_model_loaded"):
            ner_name, ner_path, ner_ok, ner_error = _probe_ner_model()
            queue["ner_model_name"] = ner_name if ner_ok else None
            queue["ner_model_path"] = ner_path
            queue["ner_model_loaded"] = ner_ok
            queue["ner_model_error"] = ner_error
        rows = _resume_rows(queue)
        _save_queue(queue)

    if not queue.get("ner_model_loaded"):
        queue["status"] = "error"
        queue["end_time"] = _utc_now()
        queue["stop_time"] = queue["end_time"]
        _save_queue(queue)
        raise SystemExit(f"NER model failed to load: {queue.get('ner_model_error')}")
    failed = {name: meta["error"] for name, meta in (queue.get("headings") or {}).items() if not meta.get("loaded")}
    if failed:
        queue["status"] = "error"
        queue["end_time"] = _utc_now()
        queue["stop_time"] = queue["end_time"]
        _save_queue(queue)
        raise SystemExit(f"Heading detector failed to load (remove it from config.Heading_Models to skip it): {failed}")

    by_id = {document.record_id: document for document in documents}
    waiting: list[dict[str, Any]] = []  # extracted, not yet in a written workbook
    last_write = time.monotonic()

    def checkpoint() -> Path:
        """Write the workbook, then record every document it holds as completed."""
        nonlocal last_write
        path = _write_workbook(queue, rows)
        for meta in waiting:
            meta["status"] = "completed"
        waiting.clear()
        last_write = time.monotonic()
        _save_queue(queue)
        return path

    try:
        for document_meta in queue["documents"]:
            if document_meta["status"] == "completed":
                continue
            record_id = document_meta["record_id"]
            document = by_id.get(record_id)
            if document is None:
                document_meta["status"] = "error"
                document_meta["error"] = "document has no OCR under OCR_Input"
                _save_queue(queue)
                continue
            document_meta["status"] = "in_progress"
            _save_queue(queue)
            logger.info("extract %s pages=%s", record_id, len(document.pages))
            try:
                result = pipeline.extract_document(document, headings=bool(queue.get("headings")))
                for name, frame in _document_sheets(queue, result, queue["model_version"]).items():
                    rows[name].append(frame)
                document_meta.update(time_seconds=result.time_seconds, error=None)
                waiting.append(document_meta)
                logger.info("done %s extract_s=%s", record_id, result.time_seconds)
            except Exception as exc:  # noqa: BLE001 - keep queue moving
                logger.exception("failed %s", record_id)
                document_meta.update(status="error", time_seconds=None, error=str(exc))
            _save_queue(queue)
            if waiting and time.monotonic() - last_write >= CHECKPOINT_SECONDS:
                checkpoint()

        workbook_path = checkpoint()
        all_ok = all(doc["status"] == "completed" for doc in queue["documents"])
        queue["status"] = "completed" if all_ok else "error"
        queue["end_time"] = _utc_now()
        queue["stop_time"] = queue["end_time"]
        _save_queue(queue)
        logger.info(
            "run %s finished status=%s docs=%s/%s pages=%s/%s total_s=%s",
            queue["run_id"],
            queue["status"],
            queue["completed_documents"],
            queue["total_docs"],
            queue["completed_pages"],
            queue["total_pages"],
            queue["total_time_seconds"],
        )
        print(json.dumps({
            "run_id": queue["run_id"],
            "status": queue["status"],
            "model_version": queue["model_version"],
            "min_pages": queue.get("min_pages", 0),
            "max_pages": queue.get("max_pages", 0),
            "queue": str(queue_path),
            "workbook": str(workbook_path),
            "completed_documents": queue["completed_documents"],
            "completed_pages": queue["completed_pages"],
            "records_without_ocr": [item["record_id"] for item in queue.get("no_ocr") or []],
            "total_time_seconds": queue["total_time_seconds"],
            "avg_time_per_doc": queue["avg_time_per_doc"],
            "avg_time_per_page": queue["avg_time_per_page"],
        }, indent=2))
        return 0 if all_ok else 1
    except KeyboardInterrupt:
        queue["status"] = "stopped"
        queue["stop_time"] = _utc_now()
        try:
            checkpoint()
        except Exception:  # noqa: BLE001
            logger.exception("could not write partial workbook")
        _save_queue(queue)
        logger.warning("stopped — resume with the same queue file")
        return 130


def main() -> int:
    parser = argparse.ArgumentParser(description="Run both extraction models on every document into one workbook.")
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Ignore any incomplete queue and start a new run.",
    )
    parser.add_argument(
        "-N",
        "--max-pages",
        type=int,
        nargs="+",
        default=[0],
        metavar="N",
        help="Page range per document: '-N N' keeps docs with <= N pages; '-N M N' keeps docs with "
        "more than M and at most N pages. N = 0 means no upper limit.",
    )
    parser.add_argument(
        "-M",
        "--min-pages",
        type=int,
        default=None,
        metavar="M",
        help="Lower page limit, exclusive: keeps docs with more than M pages (same as '-N M N').",
    )
    parser.add_argument(
        "--records-from",
        metavar="RUN",
        help="Only process the documents of an earlier run (folder name under Run_Output or a path).",
    )
    parser.add_argument(
        "--model-version",
        default=config.Default_Model_Version,
        help="Extraction model version to use (v0 = rules). Default: config.Default_Model_Version.",
    )
    args = parser.parse_args()
    if len(args.max_pages) > 2:
        parser.error("-N takes one value (N) or two (M N)")
    if len(args.max_pages) == 2 and args.min_pages is not None:
        parser.error("give the lower limit either as '-N M N' or as -M, not both")
    min_pages = args.max_pages[0] if len(args.max_pages) == 2 else (args.min_pages or 0)
    max_pages = args.max_pages[-1]
    if min_pages < 0 or max_pages < 0:
        parser.error("page limits must be >= 0")
    if max_pages and min_pages >= max_pages:
        parser.error(f"M ({min_pages}) must be below N ({max_pages}): docs need more than M and at most N pages")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ensure_models(args.model_version, registry.RULES_VERSION)
    if args.fresh and _queue_path().is_file():
        existing = _load_queue(_queue_path())
        if existing and existing.get("status") != "completed":
            existing["status"] = "abandoned"
            existing["stop_time"] = _utc_now()
            archive = config.Run_Output / f"kv_run_queue_{existing.get('run_id', 'abandoned')}.json"
            _atomic_write_json(archive, existing)
            _queue_path().unlink(missing_ok=True)
            logger.info("abandoned previous queue -> %s", archive)
    return run_all(
        fresh=args.fresh,
        min_pages=min_pages,
        max_pages=max_pages,
        records_from=args.records_from,
        model_version=args.model_version,
    )


if __name__ == "__main__":
    raise SystemExit(main())

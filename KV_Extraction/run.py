"""Run every KV extractor in one pass per page into one Excel workbook, with a resumable queue.

Each page is parsed once, its keys are found once, and every field (DOB, Member ID,
Member Name, Provider Name, E-signature, DOS, Page No) is extracted from that result.
Headings are detected on the page image by each layout detector in config.Heading_Models
and logged with the other candidates.
Overlays for every field are drawn from the same result. Documents are the unit of
work in the queue; a resumed run skips completed documents and redoes the rest.

Usage (from repo root or this folder):
  .\\.venv\\Scripts\\python.exe KV_Extraction\\run.py
  .\\.venv\\Scripts\\python.exe KV_Extraction\\run.py --fresh
  .\\.venv\\Scripts\\python.exe KV_Extraction\\run.py -N 7
  .\\.venv\\Scripts\\python.exe KV_Extraction\\run.py -N 5 10
  .\\.venv\\Scripts\\python.exe KV_Extraction\\run.py -M 5 -N 10
  .\\.venv\\Scripts\\python.exe KV_Extraction\\run.py -N 0
  .\\.venv\\Scripts\\python.exe KV_Extraction\\run.py --fresh --records-from KV_Run_20260927_130019 --model-version v0

Queue file (active run):
  {Run_Output}/kv_run_queue.json

Output:
  {Run_Output}/KV_Run_{run_id}/run.json                  (run summary, kept in sync with the queue)
  {Run_Output}/KV_Run_{run_id}/extraction.xlsx
  {Run_Output}/KV_Run_{run_id}/detail/{field}/...
  {Run_Output}/KV_Run_{run_id}/overlays/{field}/...     (per field)
  {Run_Output}/KV_Run_{run_id}/overlays/overall/...     (all fields on one image)
  {Run_Output}/KV_Run_{run_id}/overlays/heading_*/...   (heading boxes, one folder per layout detector)
  {Run_Output}/KV_Run_{run_id}/candidates/{RecordId}.csv (candidate log with features)

-N / --max-pages  [M] N   and   -M / --min-pages M:
  -N N    = documents with N pages or fewer (0 = no upper limit, every document)
  -N M N  = documents with more than M and at most N pages: -N 5 10 runs 6..10 pages
  -M M    = the same lower limit on its own (-M 5 -N 10 = -N 5 10; -M 5 alone = more than 5)

--records-from RUN:
  only the documents of an earlier run (a rerun of that batch, scored with its reviews)
--model-version V:
  extraction model version from the registry (Training/registry.py); v0 = rules

Paths: config.py holds four absolute roots (Raw_Input, OCR_Input, Output_Root, Models_Root).
Run_Output, Training_Data and Rerun_Logs are created under Output_Root on the first run.

Models: before anything runs, GLiNER and the heading detector are checked under config.Models_Root
and downloaded from Hugging Face (pinned revisions, config.Model_Sources) when missing. A trained
version ({Models_Root}/kv_ranker/vNNN) is built locally and must be copied with the models folder.
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
from Util.excel_out import build_extraction_frames, write_workbook
from Util.model import get_model
from Util.model_setup import ensure_models

import pipeline
from pipeline import FIELDS
from Heading.extract import DETECTORS, heading_fields, load_detectors
from Training import registry
from Training.features import catalog_hash
from Training.model import load_version
from Util.documents import load_local_documents

logger = logging.getLogger(__name__)

QUEUE_NAME = "kv_run_queue.json"
# Queues written before the one-pass pipeline tracked each extractor separately.
QUEUE_VERSION = "one_pass_v1"


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
    summary = folder / "run.json"
    if summary.is_file():
        data = _load_queue(summary) or {}
        records = [doc["record_id"] for doc in data.get("documents") or [] if doc.get("record_id")]
        if records:
            return records
    records = sorted(path.stem for path in (folder / "candidates").glob("*.csv"))
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


def _heading_meta(run_folder: Path) -> dict[str, Any]:
    errors = load_detectors()
    return {
        name: {
            "id": name,
            "label": DETECTORS[name].label,
            "model_path": str(config.Heading_Models[name]),
            "overlay_folder": str(run_folder / "overlays" / name),
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
    model_version: str = pipeline.MODEL_VERSION,
    source_run: str | None = None,
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
        "excel_path": str(run_folder / "extraction.xlsx"),
        "overall_overlays": str(run_folder / "overlays" / "overall"),
        "candidates_folder": str(run_folder / "candidates"),
        "model_version": model_version,
        "catalog_hash": catalog_hash(),
        "ner_model_name": ner_name if ner_ok else None,
        "ner_model_path": ner_path,
        "ner_model_loaded": ner_ok,
        "ner_model_error": ner_error,
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
        "fields": {
            spec.id: {
                "id": spec.id,
                "label": spec.label,
                "output_folder": str(run_folder / "detail" / spec.folder_prefix),
                "overlay_folder": str(run_folder / "overlays" / spec.id),
                "detail_csv": spec.detail_csv,
                "excel_prefix": spec.excel_prefix,
            }
            for spec in FIELDS
        },
        "headings": _heading_meta(run_folder),
        "documents": [
            {
                "record_id": document.record_id,
                "page_count": len(document.pages),
                "status": "pending",
                "time_seconds": None,
                "overlay_seconds": None,
                "error": None,
            }
            for document in documents
        ],
    }


def _replace_record_rows(path: Path, frame: pd.DataFrame, record_id: str) -> None:
    """Write this record's rows, dropping any earlier rows for it (safe to redo a document)."""
    frame = frame.astype(str)
    if path.is_file():
        existing = pd.read_csv(path, dtype=str, keep_default_na=False)
        if "RecordId" in existing.columns:
            existing = existing[existing["RecordId"] != record_id]
        frame = pd.concat([existing, frame], ignore_index=True)
    frame.to_csv(path, index=False)


def _write_document(queue: dict[str, Any], result: pipeline.DocumentResult) -> float:
    """Every field's detail + summary rows, then every overlay, from one extraction result.

    With a trained version the candidate log is scored first and its choices replace the
    rules' in the rows everything else is written from.
    """
    version = queue.get("model_version") or pipeline.MODEL_VERSION
    log = result.candidates(queue["run_id"], version)
    if version != pipeline.MODEL_VERSION:
        log = load_version(version).apply(log)
        pipeline.apply_model_selection(result, log)
    for spec in FIELDS:
        folder = Path(queue["fields"][spec.id]["output_folder"])
        folder.mkdir(parents=True, exist_ok=True)
        detail, summary = result.frames(spec)
        _replace_record_rows(folder / spec.detail_csv, detail, result.record_id)
        _replace_record_rows(folder / "extraction_summary.csv", summary, result.record_id)
    candidates = Path(queue.get("candidates_folder") or Path(queue["run_folder"]) / "candidates")
    candidates.mkdir(parents=True, exist_ok=True)
    log.to_csv(candidates / f"{result.record_id}.csv", index=False)
    started = time.perf_counter()
    field_dirs = {spec.id: Path(queue["fields"][spec.id]["overlay_folder"]) for spec in FIELDS}
    heading_dirs = {name: Path(meta["overlay_folder"]) for name, meta in (queue.get("headings") or {}).items()}
    pipeline.render_document_overlays(result, field_dirs, Path(queue["overall_overlays"]), heading_dirs)
    return round(time.perf_counter() - started, 3)


def _write_excel(queue: dict[str, Any]) -> Path:
    detail_dirs = {meta["excel_prefix"]: Path(meta["output_folder"]) for meta in queue["fields"].values()}
    page_counts = {doc["record_id"]: int(doc["page_count"]) for doc in queue["documents"]}
    time_seconds = {
        doc["record_id"]: float(doc["time_seconds"])
        for doc in queue["documents"]
        if doc.get("time_seconds") is not None
    }
    extraction, summary = build_extraction_frames(detail_dirs, page_counts, time_seconds)
    path = Path(queue["excel_path"])
    write_workbook(path, extraction, summary)
    logger.info("wrote workbook %s", path)
    return path


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
    model_version: str = pipeline.MODEL_VERSION,
) -> int:
    if not registry.is_runnable(model_version):
        raise SystemExit(f"Extraction model version {model_version!r} cannot run yet")
    config.make_output_folders()
    all_documents = load_local_documents(config.OCR_Input)
    if not all_documents:
        raise SystemExit(f"No OCR JSON under {config.OCR_Input}")
    if records_from:
        wanted = set(_source_records(records_from))
        all_documents = [document for document in all_documents if document.record_id in wanted]
        if not all_documents:
            raise SystemExit(f"None of the documents of {records_from} are under {config.OCR_Input}")

    page_range = _page_range_text(min_pages, max_pages)
    documents, skipped = _filter_by_pages(all_documents, min_pages, max_pages)
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
            f"(scanned {len(all_documents)} under {config.OCR_Input})"
        )

    queue_path = _queue_path()
    queue = None if fresh else _load_queue(queue_path)
    if queue and queue.get("version") != QUEUE_VERSION:
        logger.info("queue %s is from the per-extractor runner; starting a new run", queue.get("run_id"))
        _archive_queue(queue, "_legacy")
        queue = None
    if queue and queue.get("status") == "completed":
        logger.info("previous queue completed; starting a new run")
        _archive_queue(queue)
        queue = None

    if queue is None:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        queue = _new_queue(
            documents,
            run_id,
            min_pages=min_pages,
            max_pages=max_pages,
            model_version=model_version,
            source_run=Path(records_from).name if records_from else None,
        )
        Path(queue["overall_overlays"]).mkdir(parents=True, exist_ok=True)
        for meta in queue["fields"].values():
            Path(meta["output_folder"]).mkdir(parents=True, exist_ok=True)
            Path(meta["overlay_folder"]).mkdir(parents=True, exist_ok=True)
        for meta in queue["headings"].values():
            Path(meta["overlay_folder"]).mkdir(parents=True, exist_ok=True)
        _save_queue(queue)
        logger.info(
            "started run %s %s (%s docs / %s pages)",
            run_id,
            page_range,
            queue["total_docs"],
            queue["total_pages"],
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

    try:
        for document_meta in queue["documents"]:
            if document_meta["status"] == "completed":
                continue
            record_id = document_meta["record_id"]
            document = by_id.get(record_id)
            if document is None:
                document_meta["status"] = "error"
                document_meta["error"] = "document missing from OCR_Input"
                _save_queue(queue)
                continue
            document_meta["status"] = "in_progress"
            _save_queue(queue)
            logger.info("extract %s pages=%s", record_id, len(document.pages))
            try:
                result = pipeline.extract_document(document, headings=bool(queue.get("headings")))
                overlay_seconds = _write_document(queue, result)
                document_meta.update(
                    status="completed",
                    time_seconds=result.time_seconds,
                    overlay_seconds=overlay_seconds,
                    error=None,
                )
                logger.info(
                    "done %s extract_s=%s overlay_s=%s", record_id, result.time_seconds, overlay_seconds
                )
            except Exception as exc:  # noqa: BLE001 - keep queue moving
                logger.exception("failed %s", record_id)
                document_meta.update(status="error", time_seconds=None, overlay_seconds=None, error=str(exc))
            _save_queue(queue)

        all_ok = all(doc["status"] == "completed" for doc in queue["documents"])
        queue["status"] = "completed" if all_ok else "error"
        queue["end_time"] = _utc_now()
        queue["stop_time"] = queue["end_time"]
        excel_path = _write_excel(queue)
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
            "min_pages": queue.get("min_pages", 0),
            "max_pages": queue.get("max_pages", 0),
            "queue": str(queue_path),
            "excel": str(excel_path),
            "overlays_by_field": str(Path(queue["run_folder"]) / "overlays"),
            "overlays_overall": str(queue.get("overall_overlays") or ""),
            "completed_documents": queue["completed_documents"],
            "completed_pages": queue["completed_pages"],
            "total_time_seconds": queue["total_time_seconds"],
            "avg_time_per_doc": queue["avg_time_per_doc"],
            "avg_time_per_page": queue["avg_time_per_page"],
        }, indent=2))
        return 0 if all_ok else 1
    except KeyboardInterrupt:
        queue["status"] = "stopped"
        queue["stop_time"] = _utc_now()
        try:
            _write_excel(queue)
        except Exception:  # noqa: BLE001
            logger.exception("could not write partial workbook")
        _save_queue(queue)
        logger.warning("stopped — resume with the same queue file")
        return 130


def main() -> int:
    parser = argparse.ArgumentParser(description="Run all KV extractors in one pass per page.")
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
        default=pipeline.MODEL_VERSION,
        help="Extraction model version to use (v0 = rules).",
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

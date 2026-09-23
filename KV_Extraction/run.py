"""Run every KV extractor into one Excel workbook with a resumable JSON queue.

Usage (from repo root or this folder):
  .\\.venv\\Scripts\\python.exe KV_Extraction\\run.py
  .\\.venv\\Scripts\\python.exe KV_Extraction\\run.py --fresh
  .\\.venv\\Scripts\\python.exe KV_Extraction\\run.py -N 7
  .\\.venv\\Scripts\\python.exe KV_Extraction\\run.py -N 0

Queue file (active run):
  {Local_Output}/kv_run_queue.json

Output:
  {Local_Output}/KV_Run_{run_id}/extraction.xlsx
  {Local_Output}/KV_Run_{run_id}/detail/{extractor}/...
  {Local_Output}/KV_Run_{run_id}/overlays/{field}/...     (per-extractor)
  {Local_Output}/KV_Run_{run_id}/overlays/overall/...     (all fields on one image)

-N / --max-pages:
  0 = no page-count limit (process every document)
  N >= 1 = skip any document with more than N pages
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import pandas as pd

from Util import config
from Util.excel_out import build_extraction_frames, write_workbook
from Util.model import get_model
from Util.overlay import save_overlays, save_overall_overlays

import run_dob_extraction as dob_runner
import run_esig_extraction as esig_runner
import run_id_extraction as id_runner
import run_name_extraction as name_runner
import run_provider_extraction as provider_runner

logger = logging.getLogger(__name__)

QUEUE_NAME = "kv_run_queue.json"

EXTRACTORS: list[dict[str, Any]] = [
    {
        "id": "dob",
        "label": "Member DOB",
        "folder_prefix": "DOB",
        "excel_prefix": "dob",
        "detail_csv": "extraction_dob.csv",
        "field": "dob",
        "extract": dob_runner.extract_dob,
    },
    {
        "id": "member_id",
        "label": "Member ID",
        "folder_prefix": "ID",
        "excel_prefix": "ID",
        "detail_csv": "extraction_id.csv",
        "field": "member_id",
        "extract": id_runner.extract_id,
    },
    {
        "id": "name",
        "label": "Member Name",
        "folder_prefix": "Name",
        "excel_prefix": "MName",
        "detail_csv": "extraction_name.csv",
        "field": "name",
        "extract": name_runner.extract_name,
    },
    {
        "id": "provider_name",
        "label": "Provider Name",
        "folder_prefix": "Provider",
        "excel_prefix": "PName",
        "detail_csv": "extraction_provider.csv",
        "field": "provider_name",
        "extract": provider_runner.extract_provider,
    },
    {
        "id": "electronic_signature",
        "label": "Electronic Signature",
        "folder_prefix": "ESig",
        "excel_prefix": "ESig",
        "detail_csv": "extraction_esig.csv",
        "field": "electronic_signature",
        "extract": esig_runner.extract_esig,
    },
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _queue_path() -> Path:
    return config.Local_Output / QUEUE_NAME


def _run_folder(run_id: str) -> Path:
    return config.Local_Output / f"KV_Run_{run_id}"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _load_queue(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("queue unreadable (%s)", exc)
        return None
    return data if isinstance(data, dict) else None


def _page_count(documents: list) -> int:
    return sum(len(document.pages) for document in documents)


def _recompute_progress(queue: dict[str, Any]) -> None:
    extractor_ids = [item["id"] for item in EXTRACTORS]
    completed_docs = 0
    completed_pages = 0
    for document in queue["documents"]:
        statuses = [document["extractors"][ext_id]["status"] for ext_id in extractor_ids]
        if statuses and all(status == "completed" for status in statuses):
            document["status"] = "completed"
            completed_docs += 1
            completed_pages += int(document["page_count"])
        elif any(status == "error" for status in statuses):
            document["status"] = "error"
        elif any(status == "completed" for status in statuses):
            document["status"] = "in_progress"
        else:
            document["status"] = "pending"

    queue["completed_documents"] = completed_docs
    queue["completed_pages"] = completed_pages

    started = queue.get("start_time")
    if started:
        try:
            start_dt = datetime.fromisoformat(started)
            elapsed = max(0.0, (datetime.now(timezone.utc) - start_dt).total_seconds())
        except ValueError:
            elapsed = float(queue.get("total_time_seconds") or 0)
    else:
        elapsed = float(queue.get("total_time_seconds") or 0)
    queue["total_time_seconds"] = round(elapsed, 3)
    queue["avg_time_per_doc"] = round(elapsed / completed_docs, 3) if completed_docs else None
    queue["avg_time_per_page"] = round(elapsed / completed_pages, 3) if completed_pages else None

    for ext in EXTRACTORS:
        ext_id = ext["id"]
        done_docs = 0
        done_pages = 0
        for document in queue["documents"]:
            if document["extractors"][ext_id]["status"] == "completed":
                done_docs += 1
                done_pages += int(document["page_count"])
        queue["extractors"][ext_id]["completed_docs"] = done_docs
        queue["extractors"][ext_id]["completed_pages"] = done_pages
        if done_docs >= queue["total_docs"] and queue["total_docs"] > 0:
            queue["extractors"][ext_id]["status"] = "completed"
        elif done_docs > 0:
            queue["extractors"][ext_id]["status"] = "running"
        else:
            if queue["extractors"][ext_id]["status"] not in {"completed", "error"}:
                queue["extractors"][ext_id]["status"] = "pending"


def _save_queue(queue: dict[str, Any]) -> None:
    _recompute_progress(queue)
    _atomic_write_json(_queue_path(), queue)


def _probe_ner_model() -> tuple[str, str, bool, str | None]:
    model_path = Path(config.Ner_Model_Path)
    name = model_path.name
    try:
        get_model()
        return name, str(model_path), True, None
    except Exception as exc:  # noqa: BLE001 - surface load failure in queue
        return name, str(model_path), False, str(exc)


def _filter_by_max_pages(documents: list, max_pages: int) -> tuple[list, list]:
    """Keep docs with page_count <= max_pages. max_pages <= 0 means keep all."""
    if max_pages <= 0:
        return list(documents), []
    kept: list = []
    skipped: list = []
    for document in documents:
        if len(document.pages) <= max_pages:
            kept.append(document)
        else:
            skipped.append(document)
    return kept, skipped


def _new_queue(documents: list, run_id: str, *, max_pages: int = 0) -> dict[str, Any]:
    ner_name, ner_path, ner_ok, ner_error = _probe_ner_model()
    run_folder = _run_folder(run_id)
    doc_entries = []
    for document in documents:
        doc_entries.append(
            {
                "record_id": document.record_id,
                "page_count": len(document.pages),
                "status": "pending",
                "extractors": {
                    ext["id"]: {"status": "pending", "time_seconds": None, "error": None}
                    for ext in EXTRACTORS
                },
            }
        )
    queue: dict[str, Any] = {
        "run_id": run_id,
        "status": "running",
        "max_pages": int(max_pages),
        "output_root": str(config.Local_Output),
        "run_folder": str(run_folder),
        "excel_path": str(run_folder / "extraction.xlsx"),
        "overall_overlays": str(run_folder / "overlays" / "overall"),
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
        "extractors": {
            ext["id"]: {
                "id": ext["id"],
                "label": ext["label"],
                "status": "pending",
                "output_folder": str(run_folder / "detail" / ext["folder_prefix"]),
                "overlay_folder": str(run_folder / "overlays" / ext["field"]),
                "detail_csv": ext["detail_csv"],
                "excel_prefix": ext["excel_prefix"],
                "field": ext["field"],
                "completed_docs": 0,
                "completed_pages": 0,
            }
            for ext in EXTRACTORS
        },
        "documents": doc_entries,
    }
    return queue


def _append_csv(path: Path, frame: pd.DataFrame) -> None:
    if frame is None:
        return
    if frame.empty:
        if not path.is_file():
            frame.to_csv(path, index=False)
        return
    if path.is_file():
        existing = pd.read_csv(path)
        merged = pd.concat([existing, frame], ignore_index=True)
    else:
        merged = frame
    merged.to_csv(path, index=False)


def _documents_by_id(documents: list) -> dict[str, Any]:
    return {document.record_id: document for document in documents}


def _run_extractor_doc(
    extract_fn: Callable,
    document,
    folder: Path,
    overlay_folder: Path,
    detail_csv: str,
    field: str,
) -> float:
    started = time.perf_counter()
    detail, summary = extract_fn([document])
    folder.mkdir(parents=True, exist_ok=True)
    overlay_folder.mkdir(parents=True, exist_ok=True)
    _append_csv(folder / detail_csv, detail)
    _append_csv(folder / "extraction_summary.csv", summary)
    save_overlays([document], overlay_folder, field=field, into_folder=True)
    return round(time.perf_counter() - started, 3)


def _write_excel(queue: dict[str, Any]) -> Path:
    detail_dirs = {
        meta["excel_prefix"]: Path(meta["output_folder"]) for meta in queue["extractors"].values()
    }
    page_counts = {doc["record_id"]: int(doc["page_count"]) for doc in queue["documents"]}
    time_seconds: dict[str, float] = {}
    for doc in queue["documents"]:
        total = 0.0
        for job in doc["extractors"].values():
            if job.get("time_seconds") is not None:
                total += float(job["time_seconds"])
        time_seconds[doc["record_id"]] = round(total, 3)
    extraction, summary = build_extraction_frames(detail_dirs, page_counts, time_seconds)
    path = Path(queue["excel_path"])
    write_workbook(path, extraction, summary)
    logger.info("wrote workbook %s", path)
    return path


def _archive_completed_queue(queue: dict[str, Any]) -> None:
    path = _queue_path()
    if not path.is_file():
        return
    archive = config.Local_Output / f"kv_run_queue_{queue['run_id']}.json"
    path.replace(archive)
    logger.info("archived queue -> %s", archive)


def run_all(*, fresh: bool = False, max_pages: int = 0) -> int:
    config.Local_Output.mkdir(parents=True, exist_ok=True)
    all_documents = dob_runner.load_local_documents(config.OCR_Input)
    if not all_documents:
        raise SystemExit(f"No OCR JSON under {config.OCR_Input}")

    documents, skipped = _filter_by_max_pages(all_documents, max_pages)
    if skipped:
        for document in skipped:
            logger.info(
                "skip %s pages=%s (max_pages=%s)",
                document.record_id,
                len(document.pages),
                max_pages,
            )
        logger.info(
            "max_pages=%s kept %s/%s documents (%s skipped)",
            max_pages,
            len(documents),
            len(all_documents),
            len(skipped),
        )
    if not documents:
        raise SystemExit(
            f"No documents left after max_pages={max_pages} filter "
            f"(scanned {len(all_documents)} under {config.OCR_Input})"
        )

    queue_path = _queue_path()
    queue = None if fresh else _load_queue(queue_path)
    if queue and queue.get("status") == "completed":
        logger.info("previous queue completed; starting a new run")
        _archive_completed_queue(queue)
        queue = None

    if queue is None:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        queue = _new_queue(documents, run_id, max_pages=max_pages)
        Path(queue["run_folder"]).mkdir(parents=True, exist_ok=True)
        Path(queue["overall_overlays"]).mkdir(parents=True, exist_ok=True)
        for ext in EXTRACTORS:
            Path(queue["extractors"][ext["id"]]["output_folder"]).mkdir(parents=True, exist_ok=True)
            Path(queue["extractors"][ext["id"]]["overlay_folder"]).mkdir(parents=True, exist_ok=True)
        _save_queue(queue)
        logger.info(
            "started run %s max_pages=%s (%s docs / %s pages)",
            run_id,
            max_pages,
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

    by_id = _documents_by_id(documents)

    try:
        for ext in EXTRACTORS:
            ext_id = ext["id"]
            meta = queue["extractors"][ext_id]
            if meta.get("status") == "completed":
                logger.info("skip extractor %s (already completed)", ext_id)
                continue
            folder = Path(meta["output_folder"])
            overlay_folder = Path(meta.get("overlay_folder") or (Path(queue["run_folder"]) / "overlays" / ext["field"]))
            folder.mkdir(parents=True, exist_ok=True)
            overlay_folder.mkdir(parents=True, exist_ok=True)
            meta["status"] = "running"
            _save_queue(queue)
            logger.info("extractor %s -> %s", ext_id, folder)

            for document_meta in queue["documents"]:
                record_id = document_meta["record_id"]
                job = document_meta["extractors"][ext_id]
                if job["status"] == "completed":
                    continue
                document = by_id.get(record_id)
                if document is None:
                    job["status"] = "error"
                    job["error"] = "document missing from OCR_Input"
                    _save_queue(queue)
                    continue
                try:
                    elapsed = _run_extractor_doc(
                        ext["extract"],
                        document,
                        folder,
                        overlay_folder,
                        ext["detail_csv"],
                        ext["field"],
                    )
                    job["status"] = "completed"
                    job["time_seconds"] = elapsed
                    job["error"] = None
                except Exception as exc:  # noqa: BLE001 - keep queue moving
                    logger.exception("failed %s / %s", ext_id, record_id)
                    job["status"] = "error"
                    job["error"] = str(exc)
                    job["time_seconds"] = None
                _save_queue(queue)

            if all(doc["extractors"][ext_id]["status"] == "completed" for doc in queue["documents"]):
                meta["status"] = "completed"
            elif any(doc["extractors"][ext_id]["status"] == "error" for doc in queue["documents"]):
                meta["status"] = "error"
            _save_queue(queue)
            _write_excel(queue)

        overall_docs = [
            by_id[doc["record_id"]]
            for doc in queue["documents"]
            if doc["record_id"] in by_id and doc.get("status") == "completed"
        ]
        if overall_docs:
            overall_dir = Path(queue.get("overall_overlays") or (Path(queue["run_folder"]) / "overlays" / "overall"))
            save_overall_overlays(overall_docs, overall_dir)

        all_ok = all(
            queue["extractors"][ext["id"]]["status"] == "completed" for ext in EXTRACTORS
        )
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
    parser = argparse.ArgumentParser(description="Run all KV extractors with a resumable JSON queue.")
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Ignore any incomplete queue and start a new run.",
    )
    parser.add_argument(
        "-N",
        "--max-pages",
        type=int,
        default=0,
        metavar="N",
        help="Max pages per document. 0 = no limit. N>=1 skips docs with more than N pages.",
    )
    args = parser.parse_args()
    if args.max_pages < 0:
        raise SystemExit("-N / --max-pages must be >= 0")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.fresh and _queue_path().is_file():
        existing = _load_queue(_queue_path())
        if existing and existing.get("status") != "completed":
            existing["status"] = "abandoned"
            existing["stop_time"] = _utc_now()
            _recompute_progress(existing)
            archive = config.Local_Output / f"kv_run_queue_{existing.get('run_id', 'abandoned')}.json"
            _atomic_write_json(archive, existing)
            _queue_path().unlink(missing_ok=True)
            logger.info("abandoned previous queue -> %s", archive)
    return run_all(fresh=args.fresh, max_pages=args.max_pages)


if __name__ == "__main__":
    raise SystemExit(main())
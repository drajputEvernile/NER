"""Run every KV extractor with separate outputs and a resumable JSON queue.

Usage (from repo root or this folder):
  .\\.venv\\Scripts\\python.exe KV_Extraction\\run.py
  .\\.venv\\Scripts\\python.exe KV_Extraction\\run.py --fresh

Queue file (active run):
  {Local_Output}/kv_run_queue.json

Each extractor still writes its own folder:
  DOB_Extraction_{run_id}/
  ID_Extraction_{run_id}/
  Name_Extraction_{run_id}/
  Provider_Extraction_{run_id}/
  ESig_Extraction_{run_id}/
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
from Util.model import get_model
from Util.overlay import save_overlays

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
        "folder_prefix": "DOB_Extraction",
        "detail_csv": "extraction_dob.csv",
        "field": "dob",
        "extract": dob_runner.extract_dob,
    },
    {
        "id": "member_id",
        "label": "Member ID",
        "folder_prefix": "ID_Extraction",
        "detail_csv": "extraction_id.csv",
        "field": "member_id",
        "extract": id_runner.extract_id,
    },
    {
        "id": "name",
        "label": "Member Name",
        "folder_prefix": "Name_Extraction",
        "detail_csv": "extraction_name.csv",
        "field": "name",
        "extract": name_runner.extract_name,
    },
    {
        "id": "provider_name",
        "label": "Provider Name",
        "folder_prefix": "Provider_Extraction",
        "detail_csv": "extraction_provider.csv",
        "field": "provider_name",
        "extract": provider_runner.extract_provider,
    },
    {
        "id": "electronic_signature",
        "label": "Electronic Signature",
        "folder_prefix": "ESig_Extraction",
        "detail_csv": "extraction_esig.csv",
        "field": "electronic_signature",
        "extract": esig_runner.extract_esig,
    },
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _queue_path() -> Path:
    return config.Local_Output / QUEUE_NAME


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


def _new_queue(documents: list, run_id: str) -> dict[str, Any]:
    ner_name, ner_path, ner_ok, ner_error = _probe_ner_model()
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
        "output_root": str(config.Local_Output),
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
                "output_folder": str(config.Local_Output / f"{ext['folder_prefix']}_{run_id}"),
                "detail_csv": ext["detail_csv"],
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
    detail_csv: str,
    field: str,
) -> float:
    started = time.perf_counter()
    detail, summary = extract_fn([document])
    folder.mkdir(parents=True, exist_ok=True)
    _append_csv(folder / detail_csv, detail)
    _append_csv(folder / "extraction_summary.csv", summary)
    save_overlays([document], folder, field=field)
    return round(time.perf_counter() - started, 3)


def _archive_completed_queue(queue: dict[str, Any]) -> None:
    path = _queue_path()
    if not path.is_file():
        return
    archive = config.Local_Output / f"kv_run_queue_{queue['run_id']}.json"
    path.replace(archive)
    logger.info("archived queue -> %s", archive)


def run_all(*, fresh: bool = False) -> int:
    config.Local_Output.mkdir(parents=True, exist_ok=True)
    documents = dob_runner.load_local_documents(config.OCR_Input)
    if not documents:
        raise SystemExit(f"No OCR JSON under {config.OCR_Input}")

    queue_path = _queue_path()
    queue = None if fresh else _load_queue(queue_path)
    if queue and queue.get("status") == "completed":
        logger.info("previous queue completed; starting a new run")
        _archive_completed_queue(queue)
        queue = None

    if queue is None:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        queue = _new_queue(documents, run_id)
        for ext in EXTRACTORS:
            Path(queue["extractors"][ext["id"]]["output_folder"]).mkdir(parents=True, exist_ok=True)
        _save_queue(queue)
        logger.info("started run %s (%s docs / %s pages)", run_id, queue["total_docs"], queue["total_pages"])
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
            folder.mkdir(parents=True, exist_ok=True)
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

        all_ok = all(
            queue["extractors"][ext["id"]]["status"] == "completed" for ext in EXTRACTORS
        )
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
            "queue": str(queue_path),
            "completed_documents": queue["completed_documents"],
            "completed_pages": queue["completed_pages"],
            "total_time_seconds": queue["total_time_seconds"],
            "avg_time_per_doc": queue["avg_time_per_doc"],
            "avg_time_per_page": queue["avg_time_per_page"],
            "extractors": {
                ext_id: meta["output_folder"] for ext_id, meta in queue["extractors"].items()
            },
        }, indent=2))
        return 0 if all_ok else 1
    except KeyboardInterrupt:
        queue["status"] = "stopped"
        queue["stop_time"] = _utc_now()
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
    args = parser.parse_args()
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
    return run_all(fresh=args.fresh)


if __name__ == "__main__":
    raise SystemExit(main())

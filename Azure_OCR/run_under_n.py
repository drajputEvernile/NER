"""OCR queued Azure Blob records whose existing page count is at most N.

This uses the same queue, progress file, OCR implementation, and resume behavior
as ``Azure_OCR/run.py``. It never builds a queue. Records over MAX_PAGES stay
pending in the existing queue and can be processed later by ``run.py``.

Usage (from repo root):
  .\\.venv\\Scripts\\python.exe Azure_OCR\\run_under_n.py
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUN_PATH = HERE / "run.py"

_spec = importlib.util.spec_from_file_location("azure_ocr_run", RUN_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Cannot load {RUN_PATH}")
_run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_run)

logger = logging.getLogger(__name__)
MAX_PAGES = 50


def pending_records_under_n(progress: dict) -> list[str]:
    """Return existing pending/error records with pageCount <= MAX_PAGES."""
    selected: list[str] = []
    skipped = 0
    for item in progress.get("queue") or []:
        if item.get("status") not in {"pending", "error"} or not item.get("recordId"):
            continue
        try:
            page_count = int(item.get("pageCount"))
        except (TypeError, ValueError):
            logger.warning(
                "skip %s: queue item has no usable pageCount",
                item.get("recordId"),
            )
            skipped += 1
            continue
        if page_count <= MAX_PAGES:
            selected.append(str(item["recordId"]))
        else:
            skipped += 1
            logger.info(
                "skip %s (pages=%s > MAX_PAGES=%s)",
                item["recordId"],
                page_count,
                MAX_PAGES,
            )
    logger.info(
        "filtered existing queue: selected=%s skipped=%s max_pages=%s",
        len(selected),
        skipped,
        MAX_PAGES,
    )
    return selected


def load_existing_progress() -> dict:
    """Load the existing queue without rebuilding or retallying it."""
    path = _run.azure_config.PROGRESS_FILE
    if not path.is_file():
        raise SystemExit(
            f"Existing OCR queue not found at {path}. Run Azure_OCR\\run.py first."
        )
    progress = _run.read_json(path)
    if not isinstance(progress.get("queue"), list):
        raise SystemExit(f"Existing OCR queue is invalid at {path}")
    for item in progress["queue"]:
        if str(item.get("status") or "") == "running":
            item["status"] = "pending"
    progress["status"] = "running"
    _run.save_progress(progress)
    return progress


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("azure").setLevel(logging.WARNING)
    logging.getLogger("azure.identity").setLevel(logging.WARNING)

    extractor = _run.AzureReadOcrExtractor()
    if not extractor.available:
        raise SystemExit(
            "Azure Document Intelligence is not configured. "
            "Set endpoint and key in the repo-root .env"
        )
    if not _run.blob_store.storage_configured():
        raise SystemExit(
            "Azure Blob Storage is not configured. Set AZURE_STORAGE_* in the repo-root .env"
        )
    try:
        raw_prefix = _run.blob_store.require_raw_prefix()
        write_prefix = _run.blob_store.require_write_prefix()
    except RuntimeError as err:
        raise SystemExit(str(err)) from err

    try:
        _run.blob_store.ensure_blob_ready()
    except Exception as err:
        raise SystemExit(f"Azure Blob auth failed: {err}") from err

    logger.info(
        "raw images <- %s/%s/{record}/...",
        _run.blob_store.AZURE_STORAGE_CONTAINER,
        raw_prefix,
    )
    logger.info(
        "OCR JSON output -> %s/%s/{record}/{record}_final2.json",
        _run.blob_store.AZURE_STORAGE_CONTAINER,
        write_prefix,
    )
    logger.info("mode=sequential (one record, one page at a time), max_pages=%s", MAX_PAGES)

    progress = load_existing_progress()
    queue = list(progress.get("queue") or [])
    folder_count = int(progress.get("folder_count") or len(queue))
    remaining = pending_records_under_n(progress)
    if not remaining:
        progress["status"] = "done"
        _run.save_progress(progress)
        logger.info(
            "no pending folders at or under %s pages; larger pending folders remain queued",
            MAX_PAGES,
        )
        return 0

    index_by_id = {
        str(item.get("recordId") or ""): index
        for index, item in enumerate(queue, start=1)
    }
    logger.info(
        "queue remaining=%s eligible folders=%s total_pages=%s",
        len(remaining),
        folder_count,
        progress.get("page_count_total"),
    )

    try:
        for record_id in remaining:
            _run.ocr_record(
                record_id,
                index_by_id.get(record_id, 0),
                folder_count,
                progress,
                extractor,
            )
    except KeyboardInterrupt:
        progress["status"] = "stopped"
        for item in progress.get("queue") or []:
            if item.get("status") == "running":
                item["status"] = "pending"
        _run.save_progress(progress)
        logger.info("stopped; queue saved for resume")
        return 130
    except Exception:
        progress["status"] = "error"
        _run.save_progress(progress)
        raise

    progress["status"] = "done"
    _run.save_progress(progress)
    logger.info(
        "done: %s eligible folders; larger pending folders remain for Azure_OCR/run.py",
        len(remaining),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

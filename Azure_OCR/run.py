"""OCR images from Azure Blob raw prefix; write JSON to OCR_Processed.

First run snapshots the folder count and name list. Later runs resume from the
last unfinished record/page and skip anything already written in blob storage.

Blob input:
  {container}/Raw_Input/Run1/Batch2/Deid_Images/{record_id}/images...
Blob output:
  {container}/OCR_Processed/Batch1/Final2/{record_id}/{record_id}_final2.json

Usage (from repo root):
  .\\.venv\\Scripts\\python.exe Azure_OCR\\run.py
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO_ROOT / "azure_blob"))

import azure_blob_storage as blob_store
import config as azure_config
from azure_read_ocr import AzureReadOcrExtractor

logger = logging.getLogger(__name__)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def list_folders() -> list[str]:
    folders = blob_store.list_raw_record_ids()
    if not folders:
        raise SystemExit(
            f"No record folders under {blob_store.AZURE_STORAGE_CONTAINER}/"
            f"{blob_store.AZURE_STORAGE_PREFIX}"
        )
    return folders


def load_document(record_id: str) -> dict:
    data = blob_store.load_ocr_document(record_id)
    if isinstance(data, dict) and isinstance(data.get("pages"), list):
        return data
    return {
        "recordId": record_id,
        "model": "prebuilt-read",
        "pageCount": 0,
        "pages": [],
    }


def save_document(record_id: str, document: dict) -> None:
    blob_name = blob_store.save_ocr_document(record_id, document)
    logger.info("saved OCR JSON -> %s/%s", blob_store.AZURE_STORAGE_CONTAINER, blob_name)


def done_file_names(document: dict) -> set[str]:
    names: set[str] = set()
    for page in document.get("pages") or []:
        name = str(page.get("fileName") or "").strip()
        if name:
            names.add(name)
    return names


def new_progress(folders: list[str]) -> dict:
    return {
        "raw_blob_container": blob_store.AZURE_STORAGE_CONTAINER,
        "raw_blob_prefix": blob_store.AZURE_STORAGE_PREFIX,
        "ocr_blob_container": blob_store.AZURE_STORAGE_CONTAINER,
        "ocr_blob_prefix": blob_store.AZURE_STORAGE_WRITE_PREFIX,
        "folder_count": len(folders),
        "folders": folders,
        "completed": [],
        "current_record": None,
        "current_pages_done": [],
        "started_at": utc_now(),
        "updated_at": utc_now(),
        "status": "running",
        "start_index": 1,
        "stop_index": len(folders),
    }


def load_progress() -> dict:
    path = azure_config.PROGRESS_FILE
    if not path.is_file():
        folders = list_folders()
        progress = new_progress(folders)
        write_json(path, progress)
        logger.info(
            "first run: counted %s folders under %s/%s",
            progress["folder_count"],
            blob_store.AZURE_STORAGE_CONTAINER,
            blob_store.AZURE_STORAGE_PREFIX,
        )
        return progress

    progress = read_json(path)
    same_paths = (
        str(progress.get("raw_blob_container") or "") == blob_store.AZURE_STORAGE_CONTAINER
        and str(progress.get("raw_blob_prefix") or "") == blob_store.AZURE_STORAGE_PREFIX
        and str(progress.get("ocr_blob_container") or "") == blob_store.AZURE_STORAGE_CONTAINER
        and str(progress.get("ocr_blob_prefix") or "") == blob_store.AZURE_STORAGE_WRITE_PREFIX
    )
    if not same_paths or not isinstance(progress.get("folders"), list):
        folders = list_folders()
        progress = new_progress(folders)
        write_json(path, progress)
        logger.info("paths changed: new count %s folders", progress["folder_count"])
        return progress

    progress["status"] = "running"
    progress["updated_at"] = utc_now()
    write_json(path, progress)
    logger.info(
        "resume: %s/%s folders done, last record=%s",
        len(progress.get("completed") or []),
        progress.get("folder_count"),
        progress.get("current_record"),
    )
    return progress


def save_progress(progress: dict) -> None:
    progress["updated_at"] = utc_now()
    write_json(azure_config.PROGRESS_FILE, progress)


def _mark_completed(progress: dict, record_id: str) -> None:
    completed = list(progress.get("completed") or [])
    if record_id not in completed:
        completed.append(record_id)
    progress["completed"] = completed
    progress["current_record"] = None
    progress["current_pages_done"] = []
    save_progress(progress)


def pending_records(progress: dict) -> list[str]:
    completed = set(progress.get("completed") or [])
    return [name for name in progress.get("folders") or [] if name not in completed]


def ocr_record(record_id: str, folder_no: int, folder_count: int, progress: dict, extractor: AzureReadOcrExtractor) -> None:
    progress["current_record"] = record_id
    save_progress(progress)

    pages = blob_store.list_raw_page_blobs(record_id)
    if not pages:
        logger.info("skip %s (no page images)", record_id)
        _mark_completed(progress, record_id)
        return

    document = load_document(record_id)
    already = done_file_names(document)
    progress["current_pages_done"] = sorted(already)
    save_progress(progress)

    for page_number, (file_name, blob_name) in enumerate(pages, start=1):
        if file_name in already:
            logger.info(
                "skip page %s/%s folder %s/%s record=%s file=%s",
                page_number,
                len(pages),
                folder_no,
                folder_count,
                record_id,
                file_name,
            )
            continue
        logger.info(
            "ocr page %s/%s folder %s/%s record=%s file=%s",
            page_number,
            len(pages),
            folder_no,
            folder_count,
            record_id,
            file_name,
        )
        image_bytes = blob_store.download_bytes(blob_name)
        page = extractor.extract_page_bytes(image_bytes, file_name, page_number)
        document["pages"] = [item for item in document["pages"] if item.get("fileName") != file_name]
        document["pages"].append(page)
        document["pages"].sort(key=lambda item: int(item.get("pageNumber") or 0))
        save_document(record_id, document)
        already.add(file_name)
        progress["current_pages_done"] = sorted(already)
        save_progress(progress)

    _mark_completed(progress, record_id)
    logger.info("finished record %s (%s/%s)", record_id, folder_no, folder_count)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("azure").setLevel(logging.WARNING)
    logging.getLogger("azure.identity").setLevel(logging.WARNING)

    extractor = AzureReadOcrExtractor()
    if not extractor.available:
        raise SystemExit("Azure Document Intelligence is not configured. Set endpoint and key in the repo-root .env")
    if not blob_store.storage_configured():
        raise SystemExit("Azure Blob Storage is not configured. Set AZURE_STORAGE_* in the repo-root .env")

    logger.info(
        "raw images <- %s/%s/{record}/...",
        blob_store.AZURE_STORAGE_CONTAINER,
        blob_store.AZURE_STORAGE_PREFIX,
    )
    logger.info(
        "OCR JSON output -> %s/%s/{record}/{record}_final2.json",
        blob_store.AZURE_STORAGE_CONTAINER,
        blob_store.AZURE_STORAGE_WRITE_PREFIX,
    )

    progress = load_progress()
    folders = list(progress.get("folders") or [])
    folder_count = int(progress.get("folder_count") or len(folders))
    remaining = pending_records(progress)
    if not remaining:
        progress["status"] = "done"
        save_progress(progress)
        logger.info("all %s folders already complete", folder_count)
        return 0

    logger.info("start=%s stop=%s remaining=%s", progress.get("start_index"), progress.get("stop_index"), len(remaining))
    try:
        for record_id in remaining:
            folder_no = folders.index(record_id) + 1
            ocr_record(record_id, folder_no, folder_count, progress, extractor)
    except KeyboardInterrupt:
        progress["status"] = "stopped"
        save_progress(progress)
        logger.info("stopped at record=%s pages_done=%s", progress.get("current_record"), progress.get("current_pages_done"))
        return 130

    progress["status"] = "done"
    save_progress(progress)
    logger.info("done: %s/%s folders", folder_count, folder_count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

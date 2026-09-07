"""Copy record folders from the network path, then Azure OCR them.

First run snapshots the folder count and name list. Later runs resume from the
last unfinished record/page and skip anything already written.

Usage (from repo root):
  .\\.venv\\Scripts\\python.exe Azure_OCR\\run.py
"""

from __future__ import annotations

import json
import logging
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import config as azure_config
from azure_read_ocr import AzureReadOcrExtractor

logger = logging.getLogger(__name__)

SUPPORTED = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def list_folders(root: Path) -> list[str]:
    if not root.is_dir():
        raise SystemExit(f"RAW_Read_Path is not a folder: {root}")
    return sorted(path.name for path in root.iterdir() if path.is_dir())


def list_pages(record_dir: Path) -> list[Path]:
    if not record_dir.is_dir():
        return []
    return sorted(
        path
        for path in record_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED
    )


def copy_record(record_id: str) -> Path:
    source = azure_config.record_network_dir(record_id)
    dest = azure_config.record_raw_dir(record_id)
    if not source.is_dir():
        raise FileNotFoundError(f"Source record folder not found: {source}")
    dest.mkdir(parents=True, exist_ok=True)
    azure_config.record_ocr_dir(record_id).mkdir(parents=True, exist_ok=True)
    for image in list_pages(source):
        target = dest / image.name
        if target.is_file() and target.stat().st_size == image.stat().st_size:
            continue
        shutil.copy2(image, target)
        logger.info("copied %s -> %s", image, target)
    return dest


def document_path(record_id: str) -> Path:
    return azure_config.record_ocr_json(record_id)


def load_document(record_id: str) -> dict:
    path = document_path(record_id)
    if path.is_file():
        data = read_json(path)
        if isinstance(data, dict) and isinstance(data.get("pages"), list):
            return data
    return {
        "recordId": record_id,
        "model": "prebuilt-read",
        "pageCount": 0,
        "pages": [],
    }


def save_document(record_id: str, document: dict) -> None:
    document["pageCount"] = len(document.get("pages") or [])
    write_json(document_path(record_id), document)


def done_file_names(document: dict) -> set[str]:
    names: set[str] = set()
    for page in document.get("pages") or []:
        name = str(page.get("fileName") or "").strip()
        if name:
            names.add(name)
    return names


def new_progress(folders: list[str]) -> dict:
    return {
        "raw_read_path": str(azure_config.RAW_Read_Path),
        "ocr_output_path": str(azure_config.OCR_Output_path),
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
        folders = list_folders(azure_config.RAW_Read_Path)
        progress = new_progress(folders)
        write_json(path, progress)
        logger.info("first run: counted %s folders under %s", progress["folder_count"], azure_config.RAW_Read_Path)
        return progress

    progress = read_json(path)
    same_paths = (
        str(progress.get("raw_read_path") or "") == str(azure_config.RAW_Read_Path)
        and str(progress.get("ocr_output_path") or "") == str(azure_config.OCR_Output_path)
    )
    if not same_paths or not isinstance(progress.get("folders"), list):
        folders = list_folders(azure_config.RAW_Read_Path)
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

    local_dir = copy_record(record_id)
    images = list_pages(local_dir)
    if not images:
        logger.info("skip %s (no page images)", record_id)
        _mark_completed(progress, record_id)
        return

    document = load_document(record_id)
    already = done_file_names(document)
    progress["current_pages_done"] = sorted(already)
    save_progress(progress)

    for page_number, image_path in enumerate(images, start=1):
        if image_path.name in already:
            logger.info(
                "skip page %s/%s folder %s/%s record=%s file=%s",
                page_number,
                len(images),
                folder_no,
                folder_count,
                record_id,
                image_path.name,
            )
            continue
        logger.info(
            "ocr page %s/%s folder %s/%s record=%s file=%s",
            page_number,
            len(images),
            folder_no,
            folder_count,
            record_id,
            image_path.name,
        )
        page = extractor.extract_page(image_path, page_number)
        document["pages"] = [item for item in document["pages"] if item.get("fileName") != image_path.name]
        document["pages"].append(page)
        document["pages"].sort(key=lambda item: int(item.get("pageNumber") or 0))
        save_document(record_id, document)
        already.add(image_path.name)
        progress["current_pages_done"] = sorted(already)
        save_progress(progress)

    _mark_completed(progress, record_id)
    logger.info("finished record %s (%s/%s)", record_id, folder_no, folder_count)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    extractor = AzureReadOcrExtractor()
    if not extractor.available:
        raise SystemExit("Azure Document Intelligence is not configured. Set endpoint and key in the repo-root .env")

    azure_config.OCR_Output_path.mkdir(parents=True, exist_ok=True)

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

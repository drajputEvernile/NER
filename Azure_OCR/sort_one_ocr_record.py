"""Re-sort one Azure OCR JSON in blob by fileName ascending.

Set RECORD_ID below to the record folder name, then run:
  .\\.venv\\Scripts\\python.exe Azure_OCR\\sort_one_ocr_record.py
  .\\.venv\\Scripts\\python.exe Azure_OCR\\sort_one_ocr_record.py --dry-run

Optional CLI override:
  .\\.venv\\Scripts\\python.exe Azure_OCR\\sort_one_ocr_record.py --record Test1
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(REPO_ROOT / "azure_blob"))

import azure_blob_storage as blob_store

# ============================================================
# CONFIG — set the record folder name to re-sort
# ============================================================
RECORD_ID = "Test1"


def pages_need_sort(pages: list[dict]) -> bool:
    ordered = sorted(
        pages,
        key=lambda page: blob_store.filename_sort_key(str(page.get("fileName") or "")),
    )
    for index, page in enumerate(ordered, start=1):
        current = pages[index - 1]
        if str(current.get("fileName") or "") != str(page.get("fileName") or ""):
            return True
        try:
            if int(current.get("pageNumber") or 0) != index:
                return True
        except (TypeError, ValueError):
            return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Sort one blob OCR JSON by fileName ascending.")
    parser.add_argument(
        "--record",
        default="",
        help="Record folder name. Overrides RECORD_ID in this file when set.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing back to blob.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("azure").setLevel(logging.WARNING)
    logging.getLogger("azure.identity").setLevel(logging.WARNING)

    record_id = (args.record or RECORD_ID).strip()
    if not record_id:
        raise SystemExit("Set RECORD_ID in this file, or pass --record <RecordId>")

    if not blob_store.storage_configured():
        raise SystemExit("Azure Blob Storage is not configured. Set AZURE_STORAGE_* in the repo-root .env")
    try:
        prefix = blob_store.require_write_prefix()
    except RuntimeError as err:
        raise SystemExit(str(err)) from err

    blob_name = blob_store.record_ocr_blob_name(record_id)
    logging.info(
        "record=%s blob=%s/%s dry_run=%s",
        record_id,
        blob_store.AZURE_STORAGE_CONTAINER,
        blob_name,
        args.dry_run,
    )
    if blob_store.use_entra():
        logging.info("If a browser window opens, sign in with your work account.")

    document = blob_store.load_ocr_document(record_id)
    if not document:
        raise SystemExit(f"OCR JSON not found: {blob_store.AZURE_STORAGE_CONTAINER}/{blob_name}")

    pages = list(document.get("pages") or [])
    if not pages:
        raise SystemExit(f"OCR JSON has no pages: {blob_name}")

    before = [(str(p.get("fileName") or ""), p.get("pageNumber")) for p in pages]
    if not pages_need_sort(pages):
        logging.info("already sorted by fileName; nothing to do")
        logging.info("pages=%s", before)
        return 0

    blob_store.sort_ocr_pages_by_filename(document)
    after = [(str(p.get("fileName") or ""), p.get("pageNumber")) for p in document["pages"]]
    logging.info("before=%s", before)
    logging.info("after =%s", after)

    if args.dry_run:
        logging.info("dry-run only; blob not updated")
        return 0

    saved = blob_store.save_ocr_document(record_id, document)
    logging.info("updated %s/%s", blob_store.AZURE_STORAGE_CONTAINER, saved)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

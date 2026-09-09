"""Re-sort existing Azure OCR JSON in blob by fileName ascending.

For each OCR document under AZURE_STORAGE_WRITE_PREFIX:
  - sort pages by fileName (natural ascending)
  - renumber pageNumber to 1..N
  - write the JSON back to the same blob

Does not read raw images. Uses the same Entra auth as Azure_OCR.

Usage (from repo root):
  .\\.venv\\Scripts\\python.exe Azure_OCR\\sort_blob_ocr_pages.py
  .\\.venv\\Scripts\\python.exe Azure_OCR\\sort_blob_ocr_pages.py --dry-run
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

logger = logging.getLogger(__name__)


def pages_need_sort(pages: list[dict]) -> bool:
    ordered = sorted(pages, key=lambda page: blob_store.filename_sort_key(str(page.get("fileName") or "")))
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


def sort_record(record_id: str, *, dry_run: bool) -> str:
    document = blob_store.load_ocr_document(record_id)
    if not document:
        return "missing"
    pages = list(document.get("pages") or [])
    if not pages:
        return "empty"

    before = [(str(p.get("fileName") or ""), p.get("pageNumber")) for p in pages]
    if not pages_need_sort(pages):
        return "ok"

    blob_store.sort_ocr_pages_by_filename(document)
    after = [(str(p.get("fileName") or ""), p.get("pageNumber")) for p in document["pages"]]
    logger.info("record=%s before=%s", record_id, before)
    logger.info("record=%s after =%s", record_id, after)

    if dry_run:
        return "would_update"

    blob_name = blob_store.save_ocr_document(record_id, document)
    logger.info("updated %s/%s", blob_store.AZURE_STORAGE_CONTAINER, blob_name)
    return "updated"


def main() -> int:
    parser = argparse.ArgumentParser(description="Sort blob OCR JSON pages by fileName ascending.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing back to blob.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("azure").setLevel(logging.WARNING)
    logging.getLogger("azure.identity").setLevel(logging.WARNING)

    if not blob_store.storage_configured():
        raise SystemExit("Azure Blob Storage is not configured. Set AZURE_STORAGE_* in the repo-root .env")
    try:
        prefix = blob_store.require_write_prefix()
    except RuntimeError as err:
        raise SystemExit(str(err)) from err

    logger.info(
        "sorting OCR JSON under %s/%s (dry_run=%s)",
        blob_store.AZURE_STORAGE_CONTAINER,
        prefix,
        args.dry_run,
    )
    if blob_store.use_entra():
        logger.info("If a browser window opens, sign in with your work account.")

    record_ids = blob_store.list_ocr_record_ids()
    if not record_ids:
        raise SystemExit(f"No OCR records under {blob_store.AZURE_STORAGE_CONTAINER}/{prefix}")

    counts = {"ok": 0, "updated": 0, "would_update": 0, "empty": 0, "missing": 0}
    for record_id in record_ids:
        status = sort_record(record_id, dry_run=args.dry_run)
        counts[status] = counts.get(status, 0) + 1
        logger.info("record=%s status=%s", record_id, status)

    logger.info(
        "done records=%s ok=%s updated=%s would_update=%s empty=%s missing=%s",
        len(record_ids),
        counts["ok"],
        counts["updated"],
        counts["would_update"],
        counts["empty"],
        counts["missing"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

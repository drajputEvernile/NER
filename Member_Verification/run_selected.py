"""Member verification for OCR JSON docs within a max page count.

Reads OCR JSON from Azure Blob (AZURE_OCR_STORAGE_WRITE_PREFIX).
Docs with more than MAX_PAGES pages are skipped.
Writes CSVs under absolute MV_OUTPUT_PATH / NER_OUTPUT_PATH:
  {root}/{RecordId}/member_verification/{model}.csv
  {root}/{RecordId}/ner/{model}.csv

Usage (from repo root):
  .\\.venv\\Scripts\\python.exe Member_Verification\\run_selected.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "azure_blob"))
sys.path.insert(0, str(HERE))

import importlib.util

import config as mv_config
from extractors.ner_based.ner_models.catalog import by_id

_spec = importlib.util.spec_from_file_location("member_verification_run", HERE / "run.py")
if _spec is None or _spec.loader is None:
    raise ImportError(f"Cannot load {HERE / 'run.py'}")
mv_run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mv_run)

logger = logging.getLogger(__name__)


def page_count_of(document: dict | None, pages: list[dict]) -> int:
    if document and document.get("pageCount") is not None:
        try:
            return int(document["pageCount"])
        except (TypeError, ValueError):
            pass
    return len(pages)


def load_blob_document(record_id: str) -> tuple[dict | None, list[dict]]:
    import azure_blob_storage as blob_store

    document = blob_store.load_ocr_document(record_id)
    pages = list((document or {}).get("pages") or []) if document else []
    pages = sorted(pages, key=lambda page: int(page.get("pageNumber") or 0))
    return document, pages


def run_selected() -> pd.DataFrame:
    max_pages = int(mv_config.MAX_PAGES)
    system_path = mv_config.System_Input_path
    if not system_path.is_file():
        raise SystemExit(f"system_input.csv not found at {system_path}")
    system_rows, name_mode = mv_run.load_system_input(system_path)
    if not system_rows:
        raise SystemExit(f"No records in {system_path}")

    enabled_ids = mv_config.enabled_model_ids()
    if not enabled_ids:
        raise SystemExit("No NER models enabled in config/.env")

    import azure_blob_storage as blob_store

    try:
        blob_store.require_write_prefix()
    except RuntimeError as err:
        raise SystemExit(str(err)) from err
    if not blob_store.storage_configured():
        raise SystemExit("Azure Blob Storage is not configured in .env")

    record_ids = blob_store.list_ocr_record_ids()
    record_source = f"{blob_store.AZURE_STORAGE_CONTAINER}/{blob_store.AZURE_OCR_STORAGE_WRITE_PREFIX}"
    logger.info("OCR JSON source=blob %s", record_source)
    logger.info("MAX_PAGES=%s enabled_models=%s", max_pages, enabled_ids)
    logger.info("MV CSV root: %s", mv_config.MV_Output_path)
    logger.info("NER CSV root: %s", mv_config.NER_Output_path)

    frames: list[pd.DataFrame] = []
    selected = 0
    skipped = 0
    for record_id in record_ids:
        if record_id not in system_rows:
            logger.info("skip %s (not in system_input.csv)", record_id)
            continue

        document, pages = load_blob_document(record_id)
        if not pages:
            logger.info("skip %s (empty OCR JSON)", record_id)
            skipped += 1
            continue

        count = page_count_of(document, pages)
        if count > max_pages:
            logger.info("skip %s (pages=%s > MAX_PAGES=%s)", record_id, count, max_pages)
            skipped += 1
            continue

        logger.info("selected %s pages=%s", record_id, count)
        selected += 1
        member_dir = mv_config.record_mv_output_dir(record_id)
        ner_dir = mv_config.record_ner_output_dir(record_id)
        for model_id in enabled_ids:
            logger.info("ner model %s on %s", model_id, record_id)
            frame = mv_run.verify_record(
                record_id,
                pages,
                system_rows[record_id],
                name_mode,
                by_id(model_id)["id"],
                member_dir,
                ner_dir,
            )
            frames.append(frame)

    logger.info("done selected=%s skipped=%s", selected, skipped)
    if not frames:
        raise SystemExit(f"No selected records under {record_source} with pages <= {max_pages}")
    return pd.concat(frames, ignore_index=True)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    frame = run_selected()
    print(frame.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

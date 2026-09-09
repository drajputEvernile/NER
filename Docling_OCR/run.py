"""OCR every record under RAW_Read_Path with local Docling only.

Usage (from repo root):
  .\\.venv\\Scripts\\python.exe Docling_OCR\\run.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO_ROOT))

import config as ocr_config
import docling_ocr
from docling_ocr import DoclingOcrExtractor

logger = logging.getLogger(__name__)

SUPPORTED = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}


def list_record_pages(record_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in record_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED
    )


def list_raw_records(raw_dir: Path) -> list[Path]:
    if not raw_dir.is_dir():
        return []
    return sorted(path for path in raw_dir.iterdir() if path.is_dir())


def ocr_record(record_id: str, record_dir: Path) -> None:
    pages = list_record_pages(record_dir)
    if not pages:
        logger.info("skip %s (no page images)", record_id)
        return
    docling_dir = docling_ocr.record_output_dir(record_id)
    logger.info("docling ocr %s (%s pages)", record_id, len(pages))
    DoclingOcrExtractor().extract_record_outputs(pages, docling_dir, record_id)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if not docling_ocr.enabled:
        raise SystemExit("Docling OCR is disabled. Set DOCLING_OCR=true in .env")
    records = list_raw_records(ocr_config.RAW_Read_Path)
    if not records:
        raise SystemExit(f"No record folders under {ocr_config.RAW_Read_Path}")
    for record_dir in records:
        ocr_record(record_dir.name, record_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

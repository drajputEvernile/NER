"""OCR every record under RAW_Read_Path.

Usage (from repo root):
  .\\.venv\\Scripts\\python.exe "src\\OCR\\run.py"
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE / "Docling OCR"))
sys.path.insert(0, str(HERE / "Azure OCR"))

import config as ocr_config
import azure_read_ocr
import docling_ocr
from azure_read_ocr import AzureReadOcrExtractor
from docling_ocr import DoclingOcrExtractor

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
    if docling_ocr.enabled:
        docling_dir = docling_ocr.record_output_dir(record_id)
        docling = DoclingOcrExtractor()
        for image_path in pages:
            logger.info("docling ocr %s / %s", record_id, image_path.name)
            docling.extract_page_outputs(image_path, docling_dir, cache_stem=image_path.stem)
    if azure_read_ocr.enabled:
        azure_dir = azure_read_ocr.record_output_dir(record_id)
        azure = AzureReadOcrExtractor()
        for image_path in pages:
            logger.info("azure ocr %s / %s", record_id, image_path.name)
            azure.extract_page_outputs(image_path, azure_dir, cache_stem=image_path.stem)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if not docling_ocr.enabled and not azure_read_ocr.enabled:
        raise SystemExit("No OCR engine enabled. Set DOCLING_OCR or AZURE_OCR to true in .env")
    records = list_raw_records(ocr_config.RAW_Read_Path)
    if not records:
        raise SystemExit(f"No record folders under {ocr_config.RAW_Read_Path}")
    for record_dir in records:
        ocr_record(record_dir.name, record_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""OCR images from the repo Test folder with Azure prebuilt-read.

Usage (from repo root):
  .\\.venv\\Scripts\\python.exe Azure_OCR\\run.py
  .\\.venv\\Scripts\\python.exe Azure_OCR\\run.py path\\to\\page.png
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
INPUT_DIR = REPO_ROOT / "Test"
OUTPUT_DIR = HERE / "output"
SUPPORTED = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}

sys.path.insert(0, str(HERE))

from azure_read_ocr import AzureReadOcrExtractor


def iter_images(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.is_dir():
        return []
    return sorted(
        item
        for item in path.iterdir()
        if item.is_file() and item.suffix.lower() in SUPPORTED
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    source = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else INPUT_DIR
    images = iter_images(source)
    if not images:
        raise SystemExit(f"No page images found in {source}")

    extractor = AzureReadOcrExtractor()
    if not extractor.available:
        raise SystemExit(
            "Azure Document Intelligence is not configured. "
            "Set AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT and AZURE_DOCUMENT_INTELLIGENCE_KEY in the repo .env."
        )

    for image_path in images:
        record = extractor.extract_page_outputs(image_path, OUTPUT_DIR, cache_stem=image_path.stem)
        text_path = Path(record["outputs"]["text"])
        preview = text_path.read_text(encoding="utf-8")[:400] if text_path.is_file() else ""
        print(f"image: {image_path}")
        print(f"json:  {record['outputs']['json']}")
        print(f"text:  {text_path}")
        print("--- text preview ---")
        print(preview)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Documents for a run: the record folders under Raw_Input, each looked up in OCR_Input.

The raw folder says which records exist ({Raw_Input}/{RecordId}/ holding the page images); a
record's OCR is {OCR_Input}/{RecordId}/*.json (the largest JSON in the folder). A record with no
usable OCR is reported, never silently dropped.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}


@dataclass
class Document:
    record_id: str
    pages: list[dict]
    source: str


@dataclass
class MissingOcr:
    """A raw record the OCR folder has nothing usable for."""

    record_id: str
    image_count: int
    reason: str


def _natural(name: str) -> list:
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", name)]


def _load_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("skip %s (%s)", path, exc)
        return None
    return data if isinstance(data, dict) else None


def _read_document(record_id: str, folder: Path) -> tuple[Document | None, str]:
    """(document, "") or (None, why the folder has no usable OCR)."""
    jsons = sorted(path for path in folder.glob("*.json") if path.is_file())
    if not jsons:
        return None, "no OCR JSON in its OCR folder"
    path = max(jsons, key=lambda item: item.stat().st_size)
    data = _load_json(path)
    if not data:
        return None, f"{path.name} is not readable OCR JSON"
    pages = data.get("pages")
    if not isinstance(pages, list) or not pages:
        return None, f"{path.name} has no pages"
    return Document(record_id=record_id, pages=pages, source=str(path)), ""


def load_local_documents(root: Path) -> list[Document]:
    """Every record folder under `root` that holds OCR JSON (no raw-folder lookup)."""
    if not root.is_dir():
        raise SystemExit(f"OCR_Input is not a folder: {root}")
    documents: list[Document] = []
    for child in sorted((path for path in root.iterdir() if path.is_dir()), key=lambda path: _natural(path.name)):
        document, why = _read_document(child.name, child)
        if document is None:
            logger.info("skip %s (%s)", child.name, why)
            continue
        documents.append(document)
    return documents


def raw_records(raw_root: Path) -> list[tuple[str, int]]:
    """(record id, page image count) of every folder under raw_root that holds page images."""
    if not raw_root.is_dir():
        raise SystemExit(f"Raw_Input is not a folder: {raw_root}")
    records: list[tuple[str, int]] = []
    for child in sorted((path for path in raw_root.iterdir() if path.is_dir()), key=lambda path: _natural(path.name)):
        images = sum(1 for path in child.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES)
        if images:
            records.append((child.name, images))
        else:
            logger.info("skip %s (no page images in the raw folder)", child.name)
    return records


def load_documents(raw_root: Path, ocr_root: Path) -> tuple[list[Document], list[MissingOcr], list[tuple[str, int]]]:
    """(documents, raw records without usable OCR, every raw record with its image count).

    The raw folders decide which records exist and their order; each is looked up in the OCR folder
    by name (case-insensitive, like the file system). The document keeps the raw folder's name."""
    if not ocr_root.is_dir():
        raise SystemExit(f"OCR_Input is not a folder: {ocr_root}")
    ocr_folders = {path.name.casefold(): path for path in ocr_root.iterdir() if path.is_dir()}
    records = raw_records(raw_root)
    documents: list[Document] = []
    missing: list[MissingOcr] = []
    for record_id, images in records:
        folder = ocr_folders.get(record_id.casefold())
        if folder is None:
            missing.append(MissingOcr(record_id, images, "no folder of that name in OCR_Input"))
            continue
        document, why = _read_document(record_id, folder)
        if document is None:
            missing.append(MissingOcr(record_id, images, why))
            continue
        if len(document.pages) != images:
            logger.warning(
                "%s: %s page images in Raw_Input but %s pages in the OCR JSON", record_id, images, len(document.pages)
            )
        documents.append(document)
    return documents, missing, records

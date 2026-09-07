"""One JSON per record for Docling and Azure OCR."""

from __future__ import annotations

import json
from pathlib import Path


def json_path(output_dir: Path, record_id: str) -> Path:
    return output_dir / f"{record_id}.json"


def write_document(output_dir: Path, record_id: str, document: dict) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_file = json_path(output_dir, record_id)
    json_file.write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")
    return json_file


def cached_document(output_dir: Path, record_id: str) -> dict | None:
    path = json_path(output_dir, record_id)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("pages"), list) and data["pages"]:
        return data
    return None


def load_pages(ocr_dir: Path, record_id: str) -> list[dict]:
    combined = cached_document(ocr_dir, record_id)
    if combined is not None:
        return list(combined["pages"])
    return _load_legacy_pages(ocr_dir, record_id)


def page_text(page: dict) -> str:
    return str(page.get("content") or "")


def page_from_docling(result: dict, page_number: int, file_name: str) -> dict:
    nested = dict(_first_page(result))
    nested.pop("pageNumber", None)
    content = _content(result) or str(nested.pop("content", "") or "")
    return {
        "pageNumber": page_number,
        "fileName": file_name,
        "content": content,
        **nested,
    }


def page_from_azure(result: dict, page_number: int, file_name: str) -> dict:
    payload = result.get("analyzeResult") if isinstance(result.get("analyzeResult"), dict) else result
    nested = _first_page(payload)
    return {
        "pageNumber": page_number,
        "fileName": file_name,
        "content": _content(payload),
        "angle": nested.get("angle"),
        "width": nested.get("width"),
        "height": nested.get("height"),
        "unit": nested.get("unit"),
        "lines": nested.get("lines") or [],
        "words": nested.get("words") or [],
        "languages": payload.get("languages"),
        "barcodes": payload.get("barcodes"),
        "azure": result,
    }


def load_legacy_page_file(path: Path, page_number: int, file_name: str) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {"pageNumber": page_number, "fileName": file_name, "content": ""}
    if data.get("model") == "docling":
        return page_from_docling(data, page_number, file_name)
    return page_from_azure(data, page_number, file_name)


def _content(data: dict) -> str:
    if data.get("content"):
        return str(data["content"])
    nested = data.get("analyzeResult")
    if isinstance(nested, dict) and nested.get("content"):
        return str(nested["content"])
    return ""


def _first_page(data: dict) -> dict:
    pages = data.get("pages")
    if isinstance(pages, list) and pages and isinstance(pages[0], dict):
        return pages[0]
    return {}


def _load_legacy_pages(ocr_dir: Path, record_id: str) -> list[dict]:
    if not ocr_dir.is_dir():
        return []
    pages: list[dict] = []
    for index, path in enumerate(sorted(ocr_dir.glob("*.json")), start=1):
        if path.stem == record_id:
            continue
        pages.append(load_legacy_page_file(path, index, path.name.replace(".json", "")))
    return pages

"""Master Data Builder API routes + persistence (JSON + Excel)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from Util import config
from Util.geometry import group_lines, words_from_page

from Master_Data_Builder.select_records import (
    IMAGE_EXTS,
    list_eligible_records,
    load_selected_records,
    select_records,
)

router = APIRouter(prefix="/api/master", tags=["master-data"])

KEY_GROUPS = (
    "Member DOB",
    "Member ID",
    "Member Name",
    "Provider Name",
    "DOS",
    "E-Sign",
    "Page No",
    "Others",
)

SHEET_NAMES = {
    "Member DOB": "Member_DOB",
    "Member ID": "Member_ID",
    "Member Name": "Member_Name",
    "Provider Name": "Provider_Name",
    "DOS": "DOS",
    "E-Sign": "E_Sign",
    "Page No": "Page_No",
    "Others": "Others",
}


def _master_dir() -> Path:
    root = Path(config.Master_Data_Output)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _master_json_path() -> Path:
    return _master_dir() / "master_data.json"


def _master_excel_path() -> Path:
    return _master_dir() / "master_data.xlsx"


def _load_master_data() -> dict[str, Any]:
    path = _master_json_path()
    if not path.is_file():
        return {"annotations": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"annotations": []}
    if not isinstance(data, dict):
        return {"annotations": []}
    anns = data.get("annotations")
    if not isinstance(anns, list):
        data["annotations"] = []
    return data


def _save_master_data(data: dict[str, Any]) -> None:
    path = _master_json_path()
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    _write_master_excel(data.get("annotations") or [])


def _write_master_excel(annotations: list[dict[str, Any]]) -> None:
    sheets: dict[str, list[dict[str, str]]] = {name: [] for name in SHEET_NAMES.values()}
    for item in annotations:
        if not isinstance(item, dict):
            continue
        group = str(item.get("group") or "")
        sheet = SHEET_NAMES.get(group)
        if not sheet:
            continue
        row = {
            "RecordId": str(item.get("record_id") or ""),
            "FileName": str(item.get("file_name") or ""),
            "PageNumber": str(item.get("page_number") or ""),
            "KeyGroup": group,
            "Key": str(item.get("key") or ""),
            "Value": str(item.get("value") or ""),
            "Value2": str(item.get("value2") or ""),
        }
        sheets[sheet].append(row)
    path = _master_excel_path()
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for group, sheet_name in SHEET_NAMES.items():
            columns = ["RecordId", "FileName", "PageNumber", "KeyGroup", "Key", "Value", "Value2"]
            if group != "E-Sign":
                # Still keep Value2 column empty for consistency across sheets? User asked separate sheets;
                # for non-ESign Value2 can stay for alignment or drop. Keep for training simplicity.
                pass
            pd.DataFrame(sheets[sheet_name], columns=columns).to_excel(
                writer, sheet_name=sheet_name, index=False
            )


def _list_page_files(record_id: str) -> list[dict[str, str]]:
    folder = Path(config.Raw_Input) / record_id
    if not folder.is_dir():
        return []
    files = sorted(
        path
        for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_EXTS
    )
    pages: list[dict[str, str]] = []
    for index, path in enumerate(files, start=1):
        pages.append(
            {
                "page_number": str(index),
                "file_name": path.name,
            }
        )
    return pages


def _ocr_page(record_id: str, file_name: str) -> dict[str, Any] | None:
    folder = Path(config.OCR_Input) / record_id
    if not folder.is_dir():
        return None
    jsons = sorted(path for path in folder.glob("*.json") if path.is_file())
    if not jsons:
        return None
    path = max(jsons, key=lambda item: item.stat().st_size)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    pages = data.get("pages") if isinstance(data, dict) else None
    if not isinstance(pages, list):
        return None
    target = file_name.casefold()
    for page in pages:
        if not isinstance(page, dict):
            continue
        if str(page.get("fileName") or "").casefold() == target:
            return page
    # Fallback: match by page number order if fileName missing
    return None


def _page_ocr_text(page: dict[str, Any]) -> str:
    content = str(page.get("content") or "").strip()
    if content:
        return content
    words = words_from_page(page)
    if not words:
        return ""
    return "\n".join(" ".join(word.content for word in line) for line in group_lines(words))


def _page_annotations(
    annotations: list[dict[str, Any]],
    record_id: str,
    file_name: str,
    page_number: str,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in annotations:
        if not isinstance(item, dict):
            continue
        if (
            str(item.get("record_id") or "") == record_id
            and str(item.get("file_name") or "") == file_name
            and str(item.get("page_number") or "") == page_number
        ):
            rows.append(
                {
                    "group": str(item.get("group") or ""),
                    "key": str(item.get("key") or ""),
                    "value": str(item.get("value") or ""),
                    "value2": str(item.get("value2") or ""),
                }
            )
    return rows


@router.get("/key-groups")
def get_key_groups() -> list[str]:
    return list(KEY_GROUPS)


@router.get("/eligible")
def get_eligible() -> dict[str, Any]:
    rows = list_eligible_records()
    return {
        "max_pages": config.Master_Data_Max_Pages,
        "raw_input": str(config.Raw_Input),
        "count": len(rows),
        "records": rows,
    }


class SelectBody(BaseModel):
    n: int = Field(ge=1)


@router.post("/select")
def post_select(body: SelectBody) -> dict[str, Any]:
    return select_records(body.n)


@router.get("/records")
def get_records() -> dict[str, Any]:
    selected = load_selected_records()
    records = selected.get("records") or []
    if not isinstance(records, list):
        records = []
    docs: list[dict[str, Any]] = []
    for item in records:
        if not isinstance(item, dict):
            continue
        record_id = str(item.get("record_id") or "")
        if not record_id:
            continue
        pages = _list_page_files(record_id)
        docs.append(
            {
                "record_id": record_id,
                "page_count": str(item.get("page_count") or len(pages)),
                "pages": pages,
            }
        )
    return {
        "selected_path": selected.get("path"),
        "n_selected": len(docs),
        "documents": docs,
        "master_json": str(_master_json_path()),
        "master_excel": str(_master_excel_path()),
    }


@router.get("/image")
def get_master_image(record_id: str, file_name: str) -> FileResponse:
    path = Path(config.Raw_Input) / record_id / file_name
    if not path.is_file():
        raise HTTPException(404, f"image not found for {record_id}/{file_name}")
    return FileResponse(path)


@router.get("/ocr-text", response_class=PlainTextResponse)
def get_master_ocr_text(record_id: str, file_name: str) -> str:
    page = _ocr_page(record_id, file_name)
    if page is None:
        return "OCR output is not available for this page."
    text = _page_ocr_text(page)
    return text or "OCR output is empty for this page."


@router.get("/annotations")
def get_annotations(record_id: str, file_name: str, page_number: str) -> dict[str, Any]:
    data = _load_master_data()
    return {
        "record_id": record_id,
        "file_name": file_name,
        "page_number": page_number,
        "annotations": _page_annotations(
            data.get("annotations") or [], record_id, file_name, page_number
        ),
    }


class AnnotationItem(BaseModel):
    group: str
    key: str
    value: str = ""
    value2: str = ""


class SaveAnnotationsBody(BaseModel):
    record_id: str
    file_name: str
    page_number: str
    annotations: list[AnnotationItem]


@router.post("/annotations")
def save_annotations(body: SaveAnnotationsBody) -> dict[str, Any]:
    cleaned: list[dict[str, str]] = []
    for item in body.annotations:
        group = item.group.strip()
        key = item.key.strip()
        value = item.value.strip()
        value2 = item.value2.strip()
        if group not in KEY_GROUPS:
            raise HTTPException(400, f"unknown key group: {group}")
        if not key:
            continue
        if group == "E-Sign":
            if not value and not value2:
                continue
        elif not value:
            continue
        cleaned.append(
            {
                "record_id": body.record_id,
                "file_name": body.file_name,
                "page_number": body.page_number,
                "group": group,
                "key": key,
                "value": value,
                "value2": value2 if group == "E-Sign" else "",
            }
        )
    data = _load_master_data()
    existing = [
        row
        for row in (data.get("annotations") or [])
        if isinstance(row, dict)
        and not (
            str(row.get("record_id") or "") == body.record_id
            and str(row.get("file_name") or "") == body.file_name
            and str(row.get("page_number") or "") == body.page_number
        )
    ]
    data["annotations"] = existing + cleaned
    _save_master_data(data)
    return {
        "ok": True,
        "count": len(cleaned),
        "annotations": cleaned,
        "master_json": str(_master_json_path()),
        "master_excel": str(_master_excel_path()),
    }

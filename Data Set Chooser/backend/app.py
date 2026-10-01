"""Local API for the Data Set Chooser.

Records are the folders under config.Raw_Read_Path; each holds one image per page. Selecting a
record copies its page images to {config.Selected_Path}/{RecordId}. A record counts as selected
when every one of its page images is present in that folder, so the destination folder is the
only state: nothing else is stored.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import config

SUPPORTED = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}

app = FastAPI(title="Data Set Chooser")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _natural(name: str) -> list[Any]:
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", name)]


def _pages(record_dir: Path) -> list[Path]:
    try:
        files = [path for path in record_dir.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED]
    except OSError:
        return []
    return sorted(files, key=lambda path: _natural(path.name))


def _record_dirs() -> list[Path]:
    root = Path(config.Raw_Read_Path)
    if not root.is_dir():
        return []
    return sorted((path for path in root.iterdir() if path.is_dir()), key=lambda path: _natural(path.name))


def _resolve_record(record_id: str) -> tuple[Path, list[Path]]:
    if not record_id or "/" in record_id or "\\" in record_id or record_id in {".", ".."}:
        raise HTTPException(404, "record not found")
    record_dir = Path(config.Raw_Read_Path) / record_id
    pages = _pages(record_dir) if record_dir.is_dir() else []
    if not pages:
        raise HTTPException(404, "record not found")
    return record_dir, pages


def _is_selected(record_id: str, pages: list[Path]) -> bool:
    target = Path(config.Selected_Path) / record_id
    return target.is_dir() and all((target / page.name).is_file() for page in pages)


def _limit_ok(page_count: int) -> bool:
    return config.N == 0 or page_count <= config.N


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/records")
def list_records() -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    hidden = 0
    for record_dir in _record_dirs():
        pages = _pages(record_dir)
        if not pages:
            continue
        if not _limit_ok(len(pages)):
            hidden += 1
            continue
        records.append(
            {
                "record_id": record_dir.name,
                "page_count": len(pages),
                "pages": [page.name for page in pages],
                "selected": _is_selected(record_dir.name, pages),
            }
        )
    return {
        "raw_path": str(config.Raw_Read_Path),
        "selected_path": str(config.Selected_Path),
        "n": config.N,
        "records": records,
        "shown": len(records),
        "selected": sum(1 for record in records if record["selected"]),
        "hidden_over_n": hidden,
    }


@app.get("/api/image")
def get_image(record_id: str, file_name: str) -> FileResponse:
    _, pages = _resolve_record(record_id)
    for page in pages:
        if page.name == file_name:
            return FileResponse(page)
    raise HTTPException(404, f"image not found for {record_id}/{file_name}")


@app.post("/api/records/{record_id}/select")
def select_record(record_id: str) -> dict[str, Any]:
    """Copy the record's page images to Selected_Path/{record_id}. Safe to repeat: images already
    there are kept, missing ones are copied."""
    _, pages = _resolve_record(record_id)
    if not _limit_ok(len(pages)):
        raise HTTPException(409, f"{record_id} has {len(pages)} pages, more than N={config.N}")
    target = Path(config.Selected_Path) / record_id
    try:
        target.mkdir(parents=True, exist_ok=True)
        for page in pages:
            destination = target / page.name
            if destination.is_file():
                continue
            partial = destination.with_name(destination.name + ".part")
            shutil.copy2(page, partial)
            os.replace(partial, destination)
    except OSError as exc:
        raise HTTPException(500, f"Could not copy {record_id}: {exc}") from exc
    return {"record_id": record_id, "selected": _is_selected(record_id, pages), "copied_to": str(target)}


@app.delete("/api/records/{record_id}/select")
def unselect_record(record_id: str) -> dict[str, Any]:
    """Remove the copies made by select. Only a file that is the same size as its raw image is
    deleted, and the folder only if it ends up empty, so nothing else in Selected_Path is touched."""
    _, pages = _resolve_record(record_id)
    target = Path(config.Selected_Path) / record_id
    try:
        for page in pages:
            copy = target / page.name
            if copy.is_file() and copy.stat().st_size == page.stat().st_size:
                copy.unlink()
        if target.is_dir() and not any(target.iterdir()):
            target.rmdir()
    except OSError as exc:
        raise HTTPException(500, f"Could not remove {record_id}: {exc}") from exc
    return {"record_id": record_id, "selected": _is_selected(record_id, pages)}


_dist = ROOT / "frontend" / "dist"
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")

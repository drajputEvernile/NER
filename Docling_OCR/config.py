"""Docling OCR paths. Engine on/off comes from the repo-root .env.

Loads repo-root .env the same way Azure_OCR does (no env_loader dependency).
"""

from __future__ import annotations

import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent


def _load_repo_env() -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _env_str(key: str, default: str = "") -> str:
    value = os.environ.get(key)
    if value is None or not str(value).strip():
        return default
    return str(value).strip()


def _env_bool(key: str, default: bool = False) -> bool:
    raw = _env_str(key, "true" if default else "false").casefold()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


_load_repo_env()

enabled = _env_bool("DOCLING_OCR", True)
RAW_Read_Path = REPO_ROOT / "Data" / "Raw"
Docling_OCR_Output_path = REPO_ROOT / "Data" / "output"
Docling_OCR_Folder = "Docling_OCR_Output"


def record_raw_dir(record_id: str) -> Path:
    return RAW_Read_Path / record_id


def record_output_dir(record_id: str) -> Path:
    return Docling_OCR_Output_path / record_id / Docling_OCR_Folder

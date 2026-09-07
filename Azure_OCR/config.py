"""Standalone Azure OCR paths and credentials.

Set every path as a full absolute path. None of these should point inside this repo.

Secrets (endpoint, key, poll timeout) still come from the repo-root .env.
"""

from __future__ import annotations

import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent

# Source: record folders to read (network or local).
RAW_Read_Path = Path(r"E:\Projects\NER\rawi")

# Output root. For each record name we create:
#   {OCR_Output_path}\{name}\raw\
#   {OCR_Output_path}\{name}\ocr_final2\{name}_final2.json
OCR_Output_path = Path(r"E:\Projects\NER\iMGE")

RAW_FOLDER = "raw"
OCR_FOLDER = "ocr_final2"
OCR_JSON_SUFFIX = "_final2.json"

PROGRESS_FILE = OCR_Output_path / "azure_ocr_progress.json"

AZURE_POLL_TIMEOUT_SECONDS = 180


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


_load_repo_env()

AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT = (os.environ.get("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT") or "").strip()
AZURE_DOCUMENT_INTELLIGENCE_KEY = (os.environ.get("AZURE_DOCUMENT_INTELLIGENCE_KEY") or "").strip()
AZURE_POLL_TIMEOUT_SECONDS = int((os.environ.get("AZURE_POLL_TIMEOUT_SECONDS") or str(AZURE_POLL_TIMEOUT_SECONDS)).strip() or 180)


def record_network_dir(record_id: str) -> Path:
    return RAW_Read_Path / record_id


def record_work_dir(record_id: str) -> Path:
    return OCR_Output_path / record_id


def record_raw_dir(record_id: str) -> Path:
    return record_work_dir(record_id) / RAW_FOLDER


def record_ocr_dir(record_id: str) -> Path:
    return record_work_dir(record_id) / OCR_FOLDER


def record_ocr_json(record_id: str) -> Path:
    return record_ocr_dir(record_id) / f"{record_id}{OCR_JSON_SUFFIX}"

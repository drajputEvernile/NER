"""Standalone Azure OCR paths and credentials.

Reads record images from RAW_Read_Path (local/network) in place — no raw copy.
Writes OCR JSON to Azure Blob Storage under OCR_Processed/Final2.

Blob layout:
  {container}/OCR_Processed/Final2/{record_id}/{record_id}_final2.json

Secrets and storage settings come from the repo-root .env.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Source: record folders to read (network or local).
RAW_Read_Path = Path(r"E:\Projects\NER\rawi")

# Local progress file only (resume state). OCR JSON lives in blob storage.
PROGRESS_FILE = HERE / "azure_ocr_progress.json"

OCR_JSON_SUFFIX = "_final2.json"

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

AZURE_STORAGE_WRITE_PREFIX = (
    os.environ.get("AZURE_STORAGE_WRITE_PREFIX") or "OCR_Processed/Final2"
).strip().strip("/")
AZURE_STORAGE_CONTAINER = (os.environ.get("AZURE_STORAGE_CONTAINER") or "").strip()


def record_source_dir(record_id: str) -> Path:
    return RAW_Read_Path / record_id

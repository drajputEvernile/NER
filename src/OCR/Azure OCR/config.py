"""Azure OCR paths, toggle, and credentials. Secrets and engine on/off come from the repo-root .env."""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _candidate in [_HERE, *_HERE.parents]:
    if (_candidate / "env_loader.py").is_file():
        if str(_candidate) not in sys.path:
            sys.path.insert(0, str(_candidate))
        break

from env_loader import env_bool, env_int, env_str, repo_path

enabled = env_bool("AZURE_OCR", False)
RAW_Read_Path = repo_path("Data", "Raw")
Azure_OCR_Output_path = repo_path("Data", "output")
Azure_OCR_Folder = "Azure_OCR_Output"
AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT = env_str("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", "")
AZURE_DOCUMENT_INTELLIGENCE_KEY = env_str("AZURE_DOCUMENT_INTELLIGENCE_KEY", "")
AZURE_POLL_TIMEOUT_SECONDS = env_int("AZURE_POLL_TIMEOUT_SECONDS", 180)


def record_raw_dir(record_id: str) -> Path:
    return RAW_Read_Path / record_id


def record_output_dir(record_id: str) -> Path:
    return Azure_OCR_Output_path / record_id / Azure_OCR_Folder

"""Docling OCR paths and toggle. Engine on/off comes from the repo-root .env."""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _candidate in [_HERE, *_HERE.parents]:
    if (_candidate / "env_loader.py").is_file():
        if str(_candidate) not in sys.path:
            sys.path.insert(0, str(_candidate))
        break

from env_loader import env_bool, repo_path

enabled = env_bool("DOCLING_OCR", True)
RAW_Read_Path = repo_path("Data", "Raw")
Docling_OCR_Output_path = repo_path("Data", "output")
Docling_OCR_Folder = "Docling_OCR_Output"


def record_raw_dir(record_id: str) -> Path:
    return RAW_Read_Path / record_id


def record_output_dir(record_id: str) -> Path:
    return Docling_OCR_Output_path / record_id / Docling_OCR_Folder

"""OCR folder paths."""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _candidate in [_HERE, *_HERE.parents]:
    if (_candidate / "env_loader.py").is_file():
        if str(_candidate) not in sys.path:
            sys.path.insert(0, str(_candidate))
        break

from env_loader import repo_path

RAW_Read_Path = repo_path("Data", "Raw")
Docling_OCR_Output_path = repo_path("Data", "output")
Azure_OCR_Output_path = repo_path("Data", "output")
Docling_OCR_Folder = "Docling_OCR_Output"
Azure_OCR_Folder = "Azure_OCR_Output"

"""Member verification paths and NER model toggles.

OCR pages come from Azure Blob (AZURE_OCR_STORAGE_WRITE_PREFIX in .env).
Output folders use absolute paths set below (or via .env overrides).

Loads repo-root .env the same way Azure_OCR does (no env_loader dependency).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
if str(REPO_ROOT / "azure_blob") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "azure_blob"))


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


def _env_int(key: str, default: int = 0) -> int:
    raw = _env_str(key, str(default))
    try:
        return int(raw)
    except ValueError:
        return default


_load_repo_env()

gliner_large = _env_bool("GLINER_LARGE", True)
gliner_medium = _env_bool("GLINER_MEDIUM", True)
gliner_low = _env_bool("GLINER_LOW", True)

# Skip OCR JSON docs with more pages than this (run_selected).
MAX_PAGES = _env_int("MAX_PAGES", 10)

# Absolute paths (override in .env if needed).
System_Input_path = Path(
    _env_str("MV_SYSTEM_INPUT_PATH") or r"E:\Projects\NER\Data\Raw\system_input.csv"
)
MV_Output_path = Path(_env_str("MV_OUTPUT_PATH") or r"E:\Projects\NER\Data\output")
NER_Output_path = Path(_env_str("NER_OUTPUT_PATH") or r"E:\Projects\NER\Data\output")

MV_Output_Folder = "member_verification"
NER_Output_Folder = "ner"

_MODEL_FLAGS = (
    ("gliner_large", gliner_large),
    ("gliner_medium", gliner_medium),
    ("gliner_low", gliner_low),
)


def enabled_model_ids() -> list[str]:
    return [model_id for model_id, on in _MODEL_FLAGS if on]


def record_mv_output_dir(record_id: str) -> Path:
    return MV_Output_path / record_id / MV_Output_Folder


def record_ner_output_dir(record_id: str) -> Path:
    return NER_Output_path / record_id / NER_Output_Folder

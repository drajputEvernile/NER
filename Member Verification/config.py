"""Member verification paths and NER model toggles.

OCR pages come from Azure Blob (AZURE_OCR_STORAGE_WRITE_PREFIX in .env).
Output folders use absolute paths set below (or via .env overrides).
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "azure_blob") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "azure_blob"))

from env_loader import env_bool, env_int, env_str

gliner_large = env_bool("GLINER_LARGE", True)
gliner_medium = env_bool("GLINER_MEDIUM", True)
gliner_low = env_bool("GLINER_LOW", True)

# Skip OCR JSON docs with more pages than this (Member Verification run_selected).
MAX_PAGES = env_int("MAX_PAGES", 10)

# Absolute paths (override in .env if needed).
System_Input_path = Path(
    env_str("MV_SYSTEM_INPUT_PATH") or r"E:\Projects\NER\Data\Raw\system_input.csv"
)
MV_Output_path = Path(env_str("MV_OUTPUT_PATH") or r"E:\Projects\NER\Data\output")
NER_Output_path = Path(env_str("NER_OUTPUT_PATH") or r"E:\Projects\NER\Data\output")

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

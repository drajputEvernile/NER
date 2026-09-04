"""Member verification paths and NER model toggles. Model on/off comes from the repo-root .env."""

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

gliner_large = env_bool("GLINER_LARGE", True)
gliner_medium = env_bool("GLINER_MEDIUM", True)
gliner_low = env_bool("GLINER_LOW", True)
distilroberta_base_ner = env_bool("DISTILROBERTA_BASE_NER", True)

RAW_Read_Path = repo_path("Data", "Raw")
System_Input_path = repo_path("Data", "Raw", "system_input.csv")
OCR_Read_path = repo_path("Data", "output")
MV_Output_path = repo_path("Data", "output")
NER_Output_path = repo_path("Data", "output")

OCR_Read_Folder = "Docling_OCR_Output"
MV_Output_Folder = "Member_Verification_Output"
NER_Output_Folder = "ner_output"

_MODEL_FLAGS = (
    ("gliner_large", gliner_large),
    ("gliner_medium", gliner_medium),
    ("gliner_low", gliner_low),
    ("distilroberta-base-ner", distilroberta_base_ner),
)


def enabled_model_ids() -> list[str]:
    return [model_id for model_id, on in _MODEL_FLAGS if on]


def record_raw_dir(record_id: str) -> Path:
    return RAW_Read_Path / record_id


def record_ocr_read_dir(record_id: str) -> Path:
    return OCR_Read_path / record_id / OCR_Read_Folder


def record_mv_output_dir(record_id: str) -> Path:
    return MV_Output_path / record_id / MV_Output_Folder


def record_ner_output_dir(record_id: str) -> Path:
    return NER_Output_path / record_id / NER_Output_Folder

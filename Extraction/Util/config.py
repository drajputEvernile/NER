"""Shared local paths for all KV extractors. No Azure read/write.

Every path the pipeline reads or writes is set here, as a full absolute path, and nowhere else:
set them for the machine it runs on. The rest of the code reads them from this module, and the
output folders under Output_Root are derived from it.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

# Interpreter every KV entry point runs on (the repo .venv): any 3.13.x.
Python_Version = (3, 13)
if sys.version_info[:2] != Python_Version:
    raise SystemExit(
        f"KV_Extraction needs Python {'.'.join(map(str, Python_Version))}, "
        f"got {sys.version.split()[0]} ({sys.executable}). Use .\\.venv\\Scripts\\python.exe."
    )

# Code checkout (git commit recorded with each trained version). Not a data path.
REPO_ROOT = Path(__file__).resolve().parents[2]

# Raw page images: {Raw_Input}/{RecordId}/{fileName}
Raw_Input = Path(r"E:\Projects\NER\NER\Data\Raw")

# OCR JSON: {OCR_Input}/{RecordId}/*.json
OCR_Input = Path(r"E:\Projects\NER\NER\Data\OCR_Output")

# Everything the pipeline writes: run outputs, training data, rerun logs.
Output_Root = Path(r"E:\Projects\NER\NER\Data\Output")

# ---- Models (each a full absolute path)
#   Ner_Model_Path, Heading_Models  public base models; downloaded here when missing (Model_Sources below)
#   Extraction_Models               the trained models, committed to git so they travel to another machine:
#                                   KV_vNNN/ (key-value ranker: reads the OCR JSON only, never the image) and
#                                   Heading_vNNN/ (heading ranker: reads the OCR JSON and the page image)
#   Retired_Models                  models trained before this layout; kept, never loaded
# A trained version is Extraction_Models/{KV,Heading}_vNNN/manifest.json. v0 (rules) needs nothing here.
# A run with --model-version vNNN loads whichever of its two folders exist and keeps the rules for the
# other. They are trained locally from reviews (Training/train.py), never downloaded.

# GLiNER model used by every key-value extractor (gliner_low = urchade/gliner_small-v2.1)
Ner_Model_Path = Path(r"E:\Projects\NER\NER\Models\gliner_low")

# Layout detector for headings (CPU), reviewed as its own field. Empty dict = no headings.
#   layout_heron = docling-project/docling-layout-heron (Apache-2.0)
Heading_Models = {
    "heading_heron": Path(r"E:\Projects\NER\NER\Models\layout_heron"),
}

Extraction_Models = Path(r"E:\Projects\NER\NER\Models\Extraction")
Retired_Models = Path(r"E:\Projects\NER\NER\Models\_retired")

# The version a run uses when --model-version is not given (and the Review UI's Rerun preselects).
# "v0" is the rules alone.
Default_Model_Version = "v003"

for _name, _root in {
    "Raw_Input": Raw_Input,
    "OCR_Input": OCR_Input,
    "Output_Root": Output_Root,
    "Ner_Model_Path": Ner_Model_Path,
    "Extraction_Models": Extraction_Models,
    "Retired_Models": Retired_Models,
    **{f"Heading_Models[{key}]": path for key, path in Heading_Models.items()},
}.items():
    if not _root.is_absolute():
        raise SystemExit(f"config.{_name} must be an absolute path, got {_root}")

# Extraction outputs: {Run_Output}/KV_Run_{timestamp}/... and the active kv_run_queue.json.
# The Review UI lists every KV_Run_* folder under it as a batch.
Run_Output = Output_Root / "Runs"

# Trainable extraction: datasets, splits, NER exports, and the optional record_sources.csv
# (RecordId,Source) naming each record's source system / layout.
Training_Data = Output_Root / "Training"

# Logs of reruns started from the Review UI: {Rerun_Logs}/{time}_{run}_{version}.log
Rerun_Logs = Output_Root / "Rerun_Logs"


def make_output_folders() -> None:
    for folder in (Run_Output, Training_Data, Rerun_Logs):
        folder.mkdir(parents=True, exist_ok=True)


# One workbook per run holds everything it extracted and, once reviewed, the reviews:
# {Run_Output}/KV_Run_{run_id}/KV_Extraction.xlsx
Workbook_Name = "KV_Extraction.xlsx"


@dataclass(frozen=True)
class ModelSource:
    """A public Hugging Face model run.py downloads when its folder is missing. Only the model
    files come down; nothing is uploaded."""
    repo_id: str
    revision: str
    # files that must be present for the folder to count as downloaded
    required: tuple[str, ...]
    # download only these (glob patterns); empty = the whole repo
    allow: tuple[str, ...] = ()
    # a model folder nested inside another (the GLiNER encoder's tokenizer)
    subfolder: str = ""


# Pinned revisions: the exact weights the rules and trained versions were built against.
Model_Sources: dict[Path, list[ModelSource]] = {
    Ner_Model_Path: [
        ModelSource(
            "urchade/gliner_small-v2.1",
            "4e091416cf7c3481db542c2a3d26156916f3a47f",
            ("gliner_config.json", "pytorch_model.bin"),
        ),
        ModelSource(
            "microsoft/deberta-v3-small",
            "a36c739020e01763fe789b4b85e2df55d6180012",
            ("config.json", "spm.model", "tokenizer_config.json"),
            allow=("config.json", "spm.model", "tokenizer_config.json", "special_tokens_map.json"),
            subfolder="encoder",
        ),
    ],
    Heading_Models["heading_heron"]: [
        ModelSource(
            "docling-project/docling-layout-heron",
            "8f39ad3c0b4c58e9c2d2c84a38465abf757272d8",
            ("config.json", "model.safetensors", "preprocessor_config.json"),
        ),
    ],
}

"""Shared local paths for all KV extractors. No Azure read/write.

The four roots (Raw_Input, OCR_Input, Output_Root, Models_Root) are absolute paths: set them
for the machine the pipeline runs on. Every other path is derived from them.
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
Raw_Input = Path(r"E:\Projects\NER\Data\Raw")

# OCR JSON: {OCR_Input}/{RecordId}/*.json
OCR_Input = Path(r"E:\Projects\NER\Data\OCR_Output")

# Everything the pipeline writes: run outputs, training data, rerun logs.
Output_Root = Path(r"E:\Projects\NER\Data\Output")

# Model folders; a public model missing here is downloaded on the first run.
Models_Root = Path(r"E:\Projects\NER\Models")

for _name, _root in {"Raw_Input": Raw_Input, "OCR_Input": OCR_Input,
                     "Output_Root": Output_Root, "Models_Root": Models_Root}.items():
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


# Trained extraction model versions: {Model_Registry}/vNNN/manifest.json. v0 (rules) needs nothing here.
# Trained locally from reviews, so they are copied with the models folder, never downloaded.
Model_Registry = Models_Root / "kv_ranker"

# GLiNER model used by every field extractor (gliner_low = urchade/gliner_small-v2.1)
Ner_Model_Path = Models_Root / "gliner_low"

# Layout detector for headings (CPU), reviewed as its own field. Empty dict = no headings.
#   layout_heron = docling-project/docling-layout-heron (Apache-2.0)
Heading_Models = {
    "heading_heron": Models_Root / "layout_heron",
}


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

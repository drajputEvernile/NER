"""Shared local paths for all KV extractors. No Azure read/write."""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent
KV_ROOT = HERE.parent
REPO_ROOT = KV_ROOT.parent

# Raw page images: {Raw_Input}/{RecordId}/{fileName}
Raw_Input = Path(r"E:\Projects\NER\Data\Raw")

# OCR JSON: {OCR_Input}/{RecordId}/*.json
OCR_Input = Path(r"E:\Projects\NER\Data\OCR_Output")

# Extraction outputs parent: {Local_Output}/KV_Run_{timestamp}/...
# Also used by Review_UI to discover runs when Review_Run is unset.
Local_Output = Path(r"E:\Projects\NER\Data\KV_Output")

# Optional: pin Review_UI to one run folder (excel + overlays + reviews.json).
# Example: Path(r"E:\Projects\NER\Data\KV_Output\KV_Run_20260923_131233")
# Set to None to load the latest KV_Run_* under Local_Output.
Review_Run: Path | None = Path(r"E:\Projects\NER\Data\KV_Output\KV_Run_20260923_131233")

# GLiNER model used by every field extractor
Ner_Model_Path = Path(r"E:\Projects\NER\Models\gliner_medium-v2.1")

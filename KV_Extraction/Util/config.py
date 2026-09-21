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

# Extraction outputs: {Local_Output}/DOB_Extraction_{timestamp}/...
Local_Output = Path(r"E:\Projects\NER\Data\KV_Output")

# GLiNER model used by every field extractor
Ner_Model_Path = Path(r"E:\Projects\NER\Models\gliner_medium-v2.1")

"""Hugging Face download settings. Import this before huggingface_hub/docling."""

from __future__ import annotations

import os

# Windows: HF cache uses symlinks; without Developer Mode that fails.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

# hf-xet CAS reconstruction often fails with:
# "File reconstruction error: CAS Client error: error decoding response body"
# Fall back to plain HTTP downloads.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

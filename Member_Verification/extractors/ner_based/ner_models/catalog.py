"""Re-export Models/catalog for member verification extractors."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
_MODELS = _REPO_ROOT / "Models"
if str(_MODELS) not in sys.path:
    sys.path.insert(0, str(_MODELS))

from catalog import (  # noqa: E402
    MODELS,
    MODELS_DIR,
    by_id,
    model_dir,
    relink_local_paths,
)

__all__ = [
    "MODELS",
    "MODELS_DIR",
    "by_id",
    "model_dir",
    "relink_local_paths",
]

"""NER model toggles. Weights path is Models/ at the repo root."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from env_loader import env_bool, repo_path

gliner_large = env_bool("GLINER_LARGE", True)
gliner_medium = env_bool("GLINER_MEDIUM", True)
gliner_low = env_bool("GLINER_LOW", True)

NER_MODELS_PATH = repo_path("Models")

_MODEL_FLAGS = (
    ("gliner_large", gliner_large),
    ("gliner_medium", gliner_medium),
    ("gliner_low", gliner_low),
)


def enabled_model_ids() -> list[str]:
    return [model_id for model_id, on in _MODEL_FLAGS if on]

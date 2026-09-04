"""NER model toggles and local weights path. Model on/off comes from the repo-root .env."""

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

NER_MODELS_PATH = repo_path(
    "src",
    "Member Verification",
    "extractors",
    "ner_based",
    "ner_models",
    "models",
)

_MODEL_FLAGS = (
    ("gliner_large", gliner_large),
    ("gliner_medium", gliner_medium),
    ("gliner_low", gliner_low),
    ("distilroberta-base-ner", distilroberta_base_ner),
)


def enabled_model_ids() -> list[str]:
    return [model_id for model_id, on in _MODEL_FLAGS if on]

"""Load GLiNER from Util.config.Ner_Model_Path. No network calls."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

from Util import config

logger = logging.getLogger(__name__)

_MODEL = None


def _weights_present(model_dir: Path) -> bool:
    return (model_dir / "pytorch_model.bin").is_file() or (model_dir / "model.safetensors").is_file()


def _retarget_local_encoder(model_dir: Path) -> None:
    """Point GLiNER at the encoder beside the weights, not an old path.

    Also bumps a stale ``transformers_version`` on non-Mistral encoders. Older
    checkpoints ship ``\"4.0.0\"``, and transformers 5.x then falsely warns about
    a Mistral regex when ``is_local`` is not detected during tokenizer init.
    """
    encoder = (model_dir / "encoder").resolve()
    config_path = model_dir / "gliner_config.json"
    if config_path.is_file():
        data = json.loads(config_path.read_text(encoding="utf-8"))
        if data.get("model_name") != str(encoder):
            data["model_name"] = str(encoder)
            config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    for name in ("config.json", "tokenizer_config.json"):
        path = encoder / name
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        changed = False
        for key in ("model_name", "name_or_path", "_name_or_path"):
            if key in data and data.get(key) != str(encoder):
                data[key] = str(encoder)
                changed = True
        if name == "config.json":
            model_type = str(data.get("model_type") or "").casefold()
            mistral_types = {"mistral", "mistral3", "voxtral", "ministral", "pixtral"}
            version = str(data.get("transformers_version") or "")
            if model_type and model_type not in mistral_types:
                # Any declared 5.x version skips the false-positive Mistral regex path.
                if not version.startswith("5."):
                    data["transformers_version"] = "5.13.1"
                    changed = True
        if changed:
            path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _patch_torch_jit_script_eager() -> None:
    """Run Deberta helpers eagerly instead of deprecated TorchScript.

    GLiNER pulls in transformers Deberta-v2, which still decorates a few tiny
    expand/bucket helpers with ``@torch.jit.script``. Torch 2.14+ emits a
    FutureWarning for that API. Those helpers do not need scripting for our
    inference path, so bind them as plain Python callables before GLiNER loads.
    """
    import torch

    if getattr(torch.jit.script, "_kv_extraction_eager", False):
        return

    original = torch.jit.script

    def eager_script(obj=None, *args, **kwargs):
        # @torch.jit.script
        if callable(obj) and not args and not kwargs:
            return obj

        # @torch.jit.script(...)
        if obj is None:

            def decorate(fn):
                return fn

            return decorate

        return original(obj, *args, **kwargs)

    eager_script._kv_extraction_eager = True  # type: ignore[attr-defined]
    torch.jit.script = eager_script  # type: ignore[assignment]


def get_model():
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    model_dir = Path(config.Ner_Model_Path)
    if not _weights_present(model_dir):
        raise FileNotFoundError(f"GLiNER weights not found at {model_dir}")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    _retarget_local_encoder(model_dir)
    logger.info("loading GLiNER from %s", model_dir)
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            _patch_torch_jit_script_eager()
            from gliner import GLiNER

            _MODEL = GLiNER.from_pretrained(str(model_dir), local_files_only=True)
            return _MODEL
        except OSError as exc:
            last_error = exc
            logger.warning("GLiNER load attempt %s/3 failed (%s)", attempt, exc)
            for name in list(sys.modules):
                if name == "torch" or name.startswith(("torch.", "gliner", "transformers")):
                    del sys.modules[name]
            time.sleep(2)
        except Exception as exc:
            raise RuntimeError(f"GLiNER could not be loaded from {model_dir}: {exc}") from exc
    raise RuntimeError(f"GLiNER could not be loaded from {model_dir}: {last_error}") from last_error


def predict(text: str, labels: list[str], threshold: float = 0.25) -> list[dict]:
    snippet = (text or "").strip()
    if not snippet:
        return []
    hits = get_model().predict_entities(snippet, labels, threshold=threshold) or []
    return [
        {
            "text": str(hit.get("text") or "").strip(),
            "label": str(hit.get("label") or ""),
            "score": float(hit.get("score") or 0),
            "start": hit.get("start"),
            "end": hit.get("end"),
        }
        for hit in hits
    ]

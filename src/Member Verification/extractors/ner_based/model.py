"""Load local NER checkpoints. No Hugging Face or other network calls at runtime."""

from __future__ import annotations

import gc
import logging
import os
from pathlib import Path

from .ner_models.catalog import MODELS, by_id, model_dir, relink_local_paths
from .config import enabled_model_ids

logger = logging.getLogger(__name__)

_PERSON_LABELS = frozenset({"person", "per", "name", "people"})

_ENABLED_IDS = enabled_model_ids()
_ACTIVE_ID = _ENABLED_IDS[0] if _ENABLED_IDS else MODELS[0]["id"]
_LOADED: dict[str, object] = {}
_LOAD_FAILED: set[str] = set()
_OFFLINE = False


def _force_offline() -> None:
    global _OFFLINE
    if _OFFLINE:
        return
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    os.environ["HF_HUB_DISABLE_SYMLINKS"] = "1"
    _OFFLINE = True


def _looks_like_model(path: Path, kind: str) -> bool:
    if not path.is_dir():
        return False
    names = {item.name.lower() for item in path.iterdir() if item.is_file()}
    has_weights = "pytorch_model.bin" in names or "model.safetensors" in names
    if kind == "gliner":
        return has_weights and "gliner_config.json" in names
    return has_weights and "config.json" in names


def _relink(path: Path) -> None:
    relink_local_paths(path)


def use_model(model_id: str) -> None:
    global _ACTIVE_ID
    if model_id != _ACTIVE_ID:
        unload()
    _ACTIVE_ID = by_id(model_id)["id"]


def unload() -> None:
    _LOADED.clear()
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _load_gliner(path: Path):
    from gliner import GLiNER

    _relink(path)
    return GLiNER.from_pretrained(str(path), local_files_only=True)


def _load_hf_token(path: Path, spec: dict):
    from transformers import AutoModelForTokenClassification, AutoTokenizer, pipeline

    _relink(path)
    tokenizer = AutoTokenizer.from_pretrained(
        str(path),
        local_files_only=True,
        add_prefix_space=bool(spec.get("add_prefix_space")),
    )
    model = AutoModelForTokenClassification.from_pretrained(str(path), local_files_only=True)
    return pipeline(
        "ner",
        model=model,
        tokenizer=tokenizer,
        aggregation_strategy=spec.get("aggregation_strategy") or "simple",
        device=-1,
    )


def get_backend(model_id: str | None = None):
    spec = by_id(model_id or _ACTIVE_ID)
    model_id = spec["id"]
    if model_id in _LOADED:
        return spec, _LOADED[model_id]
    if model_id in _LOAD_FAILED:
        return spec, None
    _force_offline()
    path = model_dir(spec)
    if not _looks_like_model(path, spec["kind"]):
        logger.error(
            "%s is not in %s. Run: python -m extractors.ner_based.ner_models.model_downloader",
            model_id,
            path,
        )
        _LOAD_FAILED.add(model_id)
        return spec, None
    logger.info("loading %s from %s", model_id, path)
    try:
        if spec["kind"] == "gliner":
            backend = _load_gliner(path)
        elif spec["kind"] == "hf_token":
            backend = _load_hf_token(path, spec)
        else:
            raise ValueError(spec["kind"])
    except Exception:
        logger.exception("%s could not be loaded from %s", model_id, path)
        _LOAD_FAILED.add(model_id)
        return spec, None
    _LOADED[model_id] = backend
    return spec, backend


def _hit(text: str, label: str, score: float, start: int | None, end: int | None) -> dict:
    return {"text": text, "label": label, "score": score, "start": start, "end": end}


def _predict_gliner(model, text: str, labels: list[str], threshold: float) -> list[dict]:
    hits = model.predict_entities(text, labels, threshold=threshold) or []
    out: list[dict] = []
    for hit in hits:
        start = hit.get("start")
        end = hit.get("end")
        out.append(
            _hit(
                str(hit.get("text") or ""),
                str(hit.get("label") or ""),
                float(hit.get("score") or 0),
                int(start) if start is not None else None,
                int(end) if end is not None else None,
            )
        )
    return out


def _wanted_token_label(entity_group: str, labels: list[str]) -> str | None:
    group = entity_group.upper()
    want = {item.casefold() for item in labels}
    if want & _PERSON_LABELS:
        if "PER" in group:
            return "person"
        return None
    if "PER" in group:
        return "person"
    return entity_group or "MISC"


def _predict_hf_token(nlp, text: str, labels: list[str]) -> list[dict]:
    out: list[dict] = []
    for hit in nlp(text) or []:
        group = str(hit.get("entity_group") or hit.get("entity") or "")
        mapped = _wanted_token_label(group, labels)
        if mapped is None:
            continue
        start = hit.get("start")
        end = hit.get("end")
        start_i = int(start) if start is not None else None
        end_i = int(end) if end is not None else None
        span = text[start_i:end_i] if start_i is not None and end_i is not None and end_i > start_i else ""
        word = str(hit.get("word") or span or "")
        out.append(
            _hit(
                span or word,
                mapped,
                float(hit.get("score") or 0),
                start_i,
                end_i,
            )
        )
    return out


def predict_entities(
    text: str,
    labels: list[str],
    threshold: float = 0.25,
    model_id: str | None = None,
) -> list[dict]:
    snippet = (text or "").strip()
    if not snippet:
        return []
    spec, backend = get_backend(model_id)
    if backend is None:
        return []
    if spec["kind"] == "gliner":
        hits = _predict_gliner(backend, snippet, labels, threshold)
    else:
        hits = _predict_hf_token(backend, snippet, labels)
    from .log import add

    for hit in hits:
        add(
            sentence=snippet,
            value=str(hit.get("text") or ""),
            value_type=str(hit.get("label") or ""),
            value_start=hit.get("start"),
            value_end=hit.get("end"),
            ner_confidence=hit.get("score"),
        )
    return hits

"""Run a local person-name NER checkpoint on OCR text as it was saved."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from src.NER.person_name.catalog import model_by_id, model_dir

_MODEL_CACHE: dict[tuple, object] = {}

PERSON_LABELS = ["person"]
ID2LABEL_CONLL = {
    0: "O",
    1: "B-PER",
    2: "I-PER",
    3: "B-ORG",
    4: "I-ORG",
    5: "B-LOC",
    6: "I-LOC",
    7: "B-MISC",
    8: "I-MISC",
}


def _softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - np.max(logits, axis=-1, keepdims=True)
    exp = np.exp(shifted)
    return exp / np.clip(exp.sum(axis=-1, keepdims=True), 1e-12, None)


def _clean_name(name: str) -> str:
    name = re.sub(r"\s+", " ", name).strip(" #.,;:|")
    return name


_GENERIC_NAMES = frozenset(
    {
        "she",
        "he",
        "her",
        "him",
        "his",
        "hers",
        "they",
        "them",
        "their",
        "theirs",
        "patient",
        "the patient",
        "this patient",
        "female",
        "male",
        "adult",
        "large adult",
        "father",
        "mother",
        "brother",
        "sister",
        "son",
        "daughter",
        "deceased",
        "physician",
        "doctor",
        "radiologist",
        "mrn",
        "dob",
        "patient name",
        "requestor name",
        "karnofsky",
        "psych",
        "neuro",
        "brother 1",
        "sister 1",
    }
)


def _keep_name(name: str) -> bool:
    if len(name) < 2:
        return False
    if not any(ch.isalpha() for ch in name):
        return False
    lowered = name.lower().strip()
    if lowered in _GENERIC_NAMES:
        return False
    if lowered.startswith("current ") or lowered.startswith("former "):
        return False
    if "year old" in lowered or "year-old" in lowered:
        return False
    return True


def _windows(text: str, size: int, overlap: int) -> list[tuple[int, str]]:
    if len(text) <= size:
        return [(0, text)]
    step = max(1, size - overlap)
    out: list[tuple[int, str]] = []
    start = 0
    while start < len(text):
        out.append((start, text[start : start + size]))
        if start + size >= len(text):
            break
        start += step
    return out


def run_gliner(model_path: Path, text: str) -> list[dict]:
    from gliner import GLiNER

    key = ("gliner", str(model_path))
    model = _MODEL_CACHE.get(key)
    if model is None:
        model = GLiNER.from_pretrained(str(model_path), local_files_only=True)
        _MODEL_CACHE[key] = model

    out: list[dict] = []
    seen: set[tuple[int, int, str]] = set()
    for offset, chunk in _windows(text, size=900, overlap=150):
        hits = model.predict_entities(chunk, PERSON_LABELS, threshold=0.25)
        for hit in hits or []:
            label = str(hit.get("label") or "").lower()
            if label not in {"person", "per", "name", "people"}:
                continue
            name = _clean_name(hit.get("text") or "")
            if not _keep_name(name):
                continue
            start = hit.get("start")
            end = hit.get("end")
            start_i = int(start) + offset if start is not None else None
            end_i = int(end) + offset if end is not None else None
            marker = (start_i or -1, end_i or -1, name)
            if marker in seen:
                continue
            seen.add(marker)
            out.append(
                {
                    "name": name,
                    "label": hit.get("label") or "person",
                    "confidence": round(float(hit.get("score") or 0), 4),
                    "start": start_i,
                    "end": end_i,
                }
            )
    return out


def run_hf_token(model_path: Path, text: str) -> list[dict]:
    from transformers import AutoModelForTokenClassification, AutoTokenizer, pipeline

    key = ("hf_token", str(model_path))
    nlp = _MODEL_CACHE.get(key)
    if nlp is None:
        tok = AutoTokenizer.from_pretrained(str(model_path), local_files_only=True)
        model = AutoModelForTokenClassification.from_pretrained(
            str(model_path), local_files_only=True
        )
        nlp = pipeline(
            "ner",
            model=model,
            tokenizer=tok,
            aggregation_strategy="simple",
            device=-1,
        )
        _MODEL_CACHE[key] = nlp

    out: list[dict] = []
    seen: set[tuple[int, int, str]] = set()
    for offset, chunk in _windows(text, size=1800, overlap=200):
        for hit in nlp(chunk) or []:
            group = str(hit.get("entity_group") or hit.get("entity") or "").upper()
            if "PER" not in group:
                continue
            start = int(hit.get("start") or 0) + offset
            end = int(hit.get("end") or 0) + offset
            name = _clean_name(text[start:end] if end > start else (hit.get("word") or ""))
            if not _keep_name(name):
                continue
            marker = (start, end, name)
            if marker in seen:
                continue
            seen.add(marker)
            out.append(
                {
                    "name": name,
                    "label": "PER",
                    "confidence": round(float(hit.get("score") or 0), 4),
                    "start": start,
                    "end": end,
                }
            )
    return out


def _onnx_files(model_path: Path) -> tuple[Path, Path]:
    onnx = None
    for cand in (
        model_path / "onnx" / "model_quantized.onnx",
        model_path / "onnx" / "model.onnx",
        model_path / "model_quantized.onnx",
        model_path / "model.onnx",
    ):
        if cand.is_file():
            onnx = cand
            break
    tok = model_path / "tokenizer.json"
    if onnx is None or not tok.is_file():
        raise FileNotFoundError(f"ONNX/tokenizer missing under {model_path}")
    return onnx, tok


def run_onnx_conll(model_path: Path, text: str) -> list[dict]:
    import onnxruntime as ort
    from tokenizers import Tokenizer

    key = ("onnx_conll", str(model_path))
    cached = _MODEL_CACHE.get(key)
    if cached is None:
        onnx_path, tok_path = _onnx_files(model_path)
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        session = ort.InferenceSession(
            str(onnx_path), opts, providers=["CPUExecutionProvider"]
        )
        tokenizer = Tokenizer.from_file(str(tok_path))
        tokenizer.enable_truncation(max_length=256)
        cached = (session, tokenizer)
        _MODEL_CACHE[key] = cached
    session, tokenizer = cached

    out: list[dict] = []
    seen: set[tuple[int, int, str]] = set()
    for offset, chunk in _windows(text, size=800, overlap=120):
        enc = tokenizer.encode(chunk)
        ids = np.array([enc.ids[:256]], dtype=np.int64)
        mask = np.ones_like(ids)
        feeds = {"input_ids": ids, "attention_mask": mask}
        names = {item.name for item in session.get_inputs()}
        if "token_type_ids" in names:
            feeds["token_type_ids"] = np.zeros_like(ids)
        logits = session.run(None, feeds)[0][0]
        probs = _softmax(logits.astype(np.float64))
        offsets = list(enc.offsets)[: len(probs)]
        per_ids = [i for i, lab in ID2LABEL_CONLL.items() if "PER" in lab]
        spans: list[dict] = []
        cur = None
        for i, off in enumerate(offsets):
            start, end = int(off[0]), int(off[1])
            if end <= start:
                continue
            label = ID2LABEL_CONLL.get(int(np.argmax(probs[i])), "O")
            conf = float(sum(probs[i][j] for j in per_ids if j < len(probs[i])))
            if label == "B-PER":
                if cur:
                    spans.append(cur)
                cur = {"start": start, "end": end, "confs": [conf]}
            elif label == "I-PER" and cur is not None:
                cur["end"] = max(cur["end"], end)
                cur["confs"].append(conf)
            elif cur is not None:
                spans.append(cur)
                cur = None
        if cur:
            spans.append(cur)
        for span in spans:
            start = int(span["start"]) + offset
            end = int(span["end"]) + offset
            name = _clean_name(text[start:end])
            if not _keep_name(name):
                continue
            marker = (start, end, name)
            if marker in seen:
                continue
            seen.add(marker)
            out.append(
                {
                    "name": name,
                    "label": "PER",
                    "confidence": round(
                        float(sum(span["confs"]) / len(span["confs"])), 4
                    ),
                    "start": start,
                    "end": end,
                }
            )
    return out


def run_model(spec: dict | str, text: str) -> list[dict]:
    """Return person names found in OCR text without changing case."""
    if isinstance(spec, str):
        spec = model_by_id(spec)
    path = model_dir(spec)
    kind = spec["kind"]
    if kind == "gliner":
        return run_gliner(path, text)
    if kind == "hf_token":
        return run_hf_token(path, text)
    if kind == "onnx_conll":
        return run_onnx_conll(path, text)
    raise ValueError(kind)

"""Local NER checkpoints used by member verification."""

from __future__ import annotations

import json
from pathlib import Path

from ..config import NER_MODELS_PATH

MODELS_DIR = NER_MODELS_PATH

MODELS: list[dict] = [
    {
        "id": "gliner_large",
        "kind": "gliner",
        "folder": "gliner_large-v2.1",
        "repo": "urchade/gliner_large-v2.1",
        "encoder_repo": "microsoft/deberta-v3-large",
    },
    {
        "id": "gliner_medium",
        "kind": "gliner",
        "folder": "gliner_medium-v2.1",
        "repo": "urchade/gliner_medium-v2.1",
        "encoder_repo": "microsoft/deberta-v3-base",
    },
    {
        "id": "gliner_low",
        "kind": "gliner",
        "folder": "gliner_low",
        "repo": "urchade/gliner_small-v2.1",
        "encoder_repo": "microsoft/deberta-v3-small",
    },
    {
        "id": "distilroberta-base-ner",
        "kind": "hf_token",
        "folder": "distilroberta-base-ner",
        "repo": "philschmid/distilroberta-base-ner-conll2003",
        "base_repo": "distilbert/distilroberta-base",
        "aggregation_strategy": "first",
        "add_prefix_space": True,
    },
]


def by_id(model_id: str) -> dict:
    for spec in MODELS:
        if spec["id"] == model_id:
            return spec
    known = ", ".join(spec["id"] for spec in MODELS)
    raise KeyError(f"Unknown NER model {model_id!r}. Known: {known}")


def model_dir(spec: dict | str) -> Path:
    if isinstance(spec, str):
        spec = by_id(spec)
    return MODELS_DIR / spec["folder"]


def _set_name_or_path(path: Path, local: Path) -> None:
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    if not isinstance(data, dict):
        return
    resolved = str(local.resolve())
    changed = False
    for key in ("_name_or_path", "name_or_path"):
        if data.get(key) != resolved:
            data[key] = resolved
            changed = True
    tokenizer_file = local / "tokenizer.json"
    if path.name == "tokenizer_config.json" and tokenizer_file.is_file():
        resolved_tok = str(tokenizer_file.resolve())
        if data.get("tokenizer_file") not in (None, resolved_tok):
            data["tokenizer_file"] = resolved_tok
            changed = True
    if changed:
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _ensure_deberta_tokenizer(path: Path) -> None:
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    if not isinstance(data, dict):
        return
    if data.get("vocab_type") != "spm":
        return
    changed = False
    if not data.get("tokenizer_class"):
        data["tokenizer_class"] = "DebertaV2Tokenizer"
        changed = True
    defaults = {
        "unk_token": "[UNK]",
        "sep_token": "[SEP]",
        "pad_token": "[PAD]",
        "cls_token": "[CLS]",
        "mask_token": "[MASK]",
        "bos_token": "[CLS]",
        "eos_token": "[SEP]",
        "do_lower_case": False,
        "vocab_type": "spm",
    }
    for key, value in defaults.items():
        if key not in data:
            data[key] = value
            changed = True
    if changed:
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    special = path.parent / "special_tokens_map.json"
    if not special.is_file():
        special.write_text(
            json.dumps(
                {
                    "bos_token": "[CLS]",
                    "cls_token": "[CLS]",
                    "eos_token": "[SEP]",
                    "mask_token": "[MASK]",
                    "pad_token": "[PAD]",
                    "sep_token": "[SEP]",
                    "unk_token": "[UNK]",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


def relink_local_paths(root: Path) -> None:
    resolved = root.resolve()
    _set_name_or_path(root / "config.json", resolved)
    _set_name_or_path(root / "tokenizer_config.json", resolved)
    encoder = root / "encoder"
    if encoder.is_dir():
        _set_name_or_path(encoder / "config.json", encoder.resolve())
        _set_name_or_path(encoder / "tokenizer_config.json", encoder.resolve())
        config_path = root / "gliner_config.json"
        if config_path.is_file():
            data = json.loads(config_path.read_text(encoding="utf-8"))
            data["model_name"] = str(encoder.resolve())
            config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        _ensure_deberta_tokenizer(encoder / "tokenizer_config.json")

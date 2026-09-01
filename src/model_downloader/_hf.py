"""Shared Hugging Face snapshot helpers for local NER checkpoints."""

from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

TOKENIZER_PATTERNS = [
    "tokenizer*",
    "*.model",
    "spm*",
    "vocab*",
    "merges.txt",
    "special_tokens_map.json",
    "added_tokens.json",
    "config.json",
]

TOKENIZER_COPY_NAMES = [
    "tokenizer_config.json",
    "tokenizer.json",
    "special_tokens_map.json",
    "added_tokens.json",
    "spm.model",
    "sentencepiece.bpe.model",
    "vocab.txt",
    "vocab.json",
    "merges.txt",
    "config.json",
]

_ensured: set[str] = set()


def _dir_has_files(path: Path) -> bool:
    return path.is_dir() and any(child.is_file() for child in path.rglob("*"))


def _has_weight_file(path: Path) -> bool:
    names = {
        "pytorch_model.bin",
        "model.safetensors",
        "model.onnx",
        "model_quantized.onnx",
    }
    if not path.is_dir():
        return False
    for child in path.rglob("*"):
        if child.is_file() and child.name in names:
            return True
    return False


def models_are_downloaded(spec: dict) -> bool:
    from src.NER.person_name.catalog import model_dir

    dest = model_dir(spec)
    kind = spec["kind"]
    if kind == "gliner":
        config = dest / "gliner_config.json"
        if not config.is_file() or not _has_weight_file(dest):
            return False
        if spec.get("encoder_repo") and not _dir_has_files(dest / "encoder"):
            return False
        if spec.get("labels_encoder_repo") and not _has_weight_file(dest / "labels_encoder"):
            return False
        return True
    if kind == "hf_token":
        return (dest / "config.json").is_file() and _has_weight_file(dest)
    if kind == "onnx_conll":
        tok = dest / "tokenizer.json"
        onnx = dest / "onnx" / "model_quantized.onnx"
        onnx_alt = dest / "onnx" / "model.onnx"
        return tok.is_file() and (onnx.is_file() or onnx_alt.is_file())
    return _dir_has_files(dest)


def _snapshot(repo_id: str, dest: Path, allow_patterns: list[str] | None = None) -> None:
    from huggingface_hub import snapshot_download

    dest.mkdir(parents=True, exist_ok=True)
    kwargs = {"repo_id": repo_id, "local_dir": str(dest)}
    if allow_patterns:
        kwargs["allow_patterns"] = allow_patterns
    snapshot_download(**kwargs)


def _copy_tokenizer_files(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for name in TOKENIZER_COPY_NAMES:
        file = src / name
        if file.is_file():
            shutil.copy2(file, dest / name)


def _patch_json_path(
    gliner_config: Path,
    key: str,
    local_path: Path,
    nested_config: str | None = None,
) -> None:
    data = json.loads(gliner_config.read_text(encoding="utf-8"))
    resolved = str(local_path.resolve())
    data[key] = resolved
    if nested_config:
        enc_cfg = data.get(nested_config)
        if isinstance(enc_cfg, dict):
            enc_cfg["_name_or_path"] = resolved
    gliner_config.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _relink_gliner_paths(spec: dict, dest: Path) -> None:
    config = dest / "gliner_config.json"
    if not config.is_file():
        return
    encoder_dir = dest / "encoder"
    if spec.get("encoder_repo") and encoder_dir.is_dir():
        _patch_json_path(config, "model_name", encoder_dir)
    labels_dir = dest / "labels_encoder"
    if spec.get("labels_encoder_repo") and labels_dir.is_dir():
        _patch_json_path(
            config,
            "labels_encoder",
            labels_dir,
            nested_config="labels_encoder_config",
        )


def _download_spec(spec: dict, dest: Path, *, force: bool) -> None:
    if force and dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {spec['id']} from {spec['repo']} -> {dest} ...", flush=True)
    _snapshot(spec["repo"], dest)
    encoder_repo = spec.get("encoder_repo")
    if encoder_repo:
        encoder_dir = dest / "encoder"
        print(f"  encoder tokenizer {encoder_repo} -> {encoder_dir}", flush=True)
        _snapshot(encoder_repo, encoder_dir, TOKENIZER_PATTERNS)
        _copy_tokenizer_files(encoder_dir, dest)
    labels_repo = spec.get("labels_encoder_repo")
    if labels_repo:
        labels_dir = dest / "labels_encoder"
        print(f"  labels encoder {labels_repo} -> {labels_dir}", flush=True)
        _snapshot(labels_repo, labels_dir)
    _relink_gliner_paths(spec, dest)
    print(f"NER model downloaded: {dest}", flush=True)


def ensure_ner_model(spec: dict | str, *, force: bool = False) -> Path:
    from src.NER.person_name.catalog import model_by_id, model_dir

    if isinstance(spec, str):
        spec = model_by_id(spec)
    dest = model_dir(spec)
    model_id = spec["id"]
    if not force and models_are_downloaded(spec):
        if model_id not in _ensured:
            print(f"model already downloaded: {dest}", flush=True)
        _ensured.add(model_id)
        return dest
    _download_spec(spec, dest, force=force)
    _ensured.add(model_id)
    return dest


def ensure_all_ner_models(*, force: bool = False) -> list[Path]:
    from src.NER.person_name.catalog import MODELS

    paths: list[Path] = []
    for spec in MODELS:
        module = importlib.import_module(f"src.model_downloader.{spec['downloader']}")
        paths.append(module.ensure_models(force=force))
    return paths


def downloader_main(spec_id: str, argv: list[str] | None = None) -> int:
    del argv
    try:
        ensure_ner_model(spec_id)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0

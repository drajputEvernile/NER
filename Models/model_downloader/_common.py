"""Shared Hugging Face snapshot helpers. Used only while downloading."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

from huggingface_hub import snapshot_download

HERE = Path(__file__).resolve().parent
MODELS_ROOT = HERE.parent
if str(MODELS_ROOT) not in sys.path:
    sys.path.insert(0, str(MODELS_ROOT))

WEIGHT_IGNORE = (
    "*.bin",
    "*.safetensors",
    "*.h5",
    "*.ot",
    "*.msgpack",
    "*.onnx",
    "*.tflite",
    "*.pt",
    "*.ckpt",
    "flax_model*",
    "tf_model*",
    "rust_model*",
)


def snapshot(
    repo_id: str,
    local_dir: Path,
    *,
    force: bool = False,
    allow_patterns: list[str] | None = None,
    ignore_patterns: list[str] | None = None,
) -> None:
    last_error: Exception | None = None
    kwargs: dict = {
        "repo_id": repo_id,
        "local_dir": str(local_dir),
        "force_download": force,
        "max_workers": 1,
    }
    if allow_patterns is not None:
        kwargs["allow_patterns"] = allow_patterns
    if ignore_patterns is not None:
        kwargs["ignore_patterns"] = ignore_patterns
    for attempt in range(1, 6):
        try:
            snapshot_download(**kwargs)
            return
        except Exception as exc:
            last_error = exc
            wait = min(30, 2**attempt)
            print(f"{repo_id} attempt {attempt}/5 failed: {exc}")
            print(f"retrying in {wait}s")
            time.sleep(wait)
            kwargs["force_download"] = True
    raise last_error or RuntimeError(f"could not download {repo_id}")


def _encoder_ready(encoder: Path) -> bool:
    config = encoder / "config.json"
    tokenizer_config = encoder / "tokenizer_config.json"
    spm = encoder / "spm.model"
    return config.is_file() and tokenizer_config.is_file() and spm.is_file()


def download_gliner(spec: dict, *, force: bool = False) -> Path:
    from catalog import model_dir, relink_local_paths

    dest = model_dir(spec)
    dest.mkdir(parents=True, exist_ok=True)
    print(f"downloading {spec['id']} from {spec['repo']} -> {dest}")
    snapshot(spec["repo"], dest, force=force)
    encoder_repo = spec.get("encoder_repo")
    if encoder_repo:
        encoder = dest / "encoder"
        encoder.mkdir(parents=True, exist_ok=True)
        print(f"  encoder tokenizer {encoder_repo} -> {encoder}")
        snapshot(encoder_repo, encoder, force=force, ignore_patterns=list(WEIGHT_IGNORE))
        if not _encoder_ready(encoder):
            print(f"  encoder tokenizer incomplete, re-downloading {encoder_repo}")
            snapshot(encoder_repo, encoder, force=True, ignore_patterns=list(WEIGHT_IGNORE))
        if not _encoder_ready(encoder):
            raise RuntimeError(f"encoder tokenizer incomplete at {encoder}")
    relink_local_paths(dest)
    print(f"downloaded {spec['repo']} -> {dest}")
    return dest

"""Download Docling models into Models/Docling_OCR at the repo root."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from src.model_downloader import _env  # noqa: F401  sets HF download env vars

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCLING_MODELS_DIR = REPO_ROOT / "Models" / "Docling_OCR"

# Folders created by docling.utils.model_downloader.download_models for the
# layout, table, and RapidOCR engines this pipeline actually uses.
REQUIRED_FOLDERS = (
    "docling-project--docling-layout-heron",
    "docling-project--docling-layout-heron-onnx",
    "docling-project--docling-models",
    "RapidOcr",
)

_ensured = False


def _dir_has_files(path: Path) -> bool:
    return path.is_dir() and any(child.is_file() for child in path.rglob("*"))


def models_are_downloaded(models_dir: Path | None = None) -> bool:
    models_dir = Path(models_dir or DOCLING_MODELS_DIR)
    return all(_dir_has_files(models_dir / name) for name in REQUIRED_FOLDERS)


def ensure_docling_models(
    models_dir: Path | None = None,
    *,
    force: bool = False,
) -> Path:
    """Download Docling models if they are missing. Safe to call more than once."""
    global _ensured
    models_dir = Path(models_dir or DOCLING_MODELS_DIR)

    if not force and models_are_downloaded(models_dir):
        if not _ensured:
            print(f"model already downloaded: {models_dir}", flush=True)
        _ensured = True
        return models_dir

    print(f"Downloading Docling models into {models_dir} ...", flush=True)
    from docling.utils.model_downloader import download_models

    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            download_models(
                output_dir=models_dir,
                force=force,
                progress=True,
                with_layout=True,
                with_tableformer=True,
                with_code_formula=False,
                with_picture_classifier=False,
                with_rapidocr=True,
                rapidocr_models=["onnxruntime:english"],
                with_easyocr=False,
            )
            last_error = None
            break
        except Exception as exc:
            last_error = exc
            print(
                f"  Docling download failed (attempt {attempt}/3): {exc}",
                flush=True,
            )
            if attempt < 3:
                time.sleep(3 * attempt)
    if last_error is not None:
        raise last_error
    _ensured = True
    print(f"Docling models downloaded: {models_dir}", flush=True)
    return models_dir


def main(argv: list[str] | None = None) -> int:
    del argv
    try:
        ensure_docling_models()
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

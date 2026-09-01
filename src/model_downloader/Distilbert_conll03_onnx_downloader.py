"""Download 10_distilbert-conll03-onnx into Models/NER. Pass --force to re-download."""

from __future__ import annotations

from pathlib import Path

from src.model_downloader._hf import downloader_main, ensure_ner_model

SPEC_ID = "10_distilbert-conll03-onnx"


def ensure_models(*, force: bool = False) -> Path:
    return ensure_ner_model(SPEC_ID, force=force)


def main(argv: list[str] | None = None) -> int:
    return downloader_main(SPEC_ID, argv)


if __name__ == "__main__":
    raise SystemExit(main())

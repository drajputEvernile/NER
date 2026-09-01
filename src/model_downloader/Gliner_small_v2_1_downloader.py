"""Download 01_gliner_small-v2.1 into Models/NER."""

from __future__ import annotations

from pathlib import Path

from src.model_downloader._hf import downloader_main, ensure_ner_model

SPEC_ID = "01_gliner_small-v2.1"


def ensure_models(*, force: bool = False) -> Path:
    return ensure_ner_model(SPEC_ID, force=force)


def main(argv: list[str] | None = None) -> int:
    return downloader_main(SPEC_ID, argv)


if __name__ == "__main__":
    raise SystemExit(main())

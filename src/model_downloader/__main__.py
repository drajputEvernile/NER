import sys

from src.model_downloader._hf import ensure_all_ner_models, wants_force
from src.model_downloader.Docling_downloader import ensure_docling_models


def main(argv: list[str] | None = None) -> int:
    force = wants_force(argv)
    try:
        ensure_docling_models(force=force)
        ensure_all_ner_models(force=force)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

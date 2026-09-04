"""Download philschmid/distilroberta-base-ner-conll2003 into ner_models/models."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

if __package__:
    from ..catalog import by_id
    from ._common import download_hf_token
else:
    sys.path.insert(0, str(HERE))
    sys.path.insert(0, str(HERE.parent))
    from catalog import by_id
    from _common import download_hf_token

SPEC = by_id("distilroberta-base-ner")


def download(*, force: bool = False) -> Path:
    return download_hf_token(SPEC, force=force)


def main() -> int:
    download()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

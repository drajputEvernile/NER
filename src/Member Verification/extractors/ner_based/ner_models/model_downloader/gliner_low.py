"""Download urchade/gliner_small-v2.1 into ner_models/models/gliner_low."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

if __package__:
    from ..catalog import by_id
    from ._common import download_gliner
else:
    sys.path.insert(0, str(HERE))
    sys.path.insert(0, str(HERE.parent))
    from catalog import by_id
    from _common import download_gliner

SPEC = by_id("gliner_low")


def download(*, force: bool = False) -> Path:
    return download_gliner(SPEC, force=force)


def main() -> int:
    download()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

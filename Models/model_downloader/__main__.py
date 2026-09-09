"""Download every local NER model into Models/."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

if __package__:
    from . import gliner_large_v2_1, gliner_low, gliner_medium_v2_1
else:
    sys.path.insert(0, str(HERE))
    import gliner_large_v2_1
    import gliner_low
    import gliner_medium_v2_1

DOWNLOADERS = (
    gliner_large_v2_1,
    gliner_medium_v2_1,
    gliner_low,
)


def download_all(*, force: bool = False) -> None:
    for module in DOWNLOADERS:
        module.download(force=force)


def main() -> int:
    download_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

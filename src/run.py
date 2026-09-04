"""Run member verification from Data/Raw through Docling OCR.

Usage (from repo root):
  .\\"OLD NER"\\.venv\\Scripts\\python.exe src\\run.py
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path

SRC = Path(__file__).resolve().parent
_EXTRACT = SRC / "Member Verification" / "run.py"

_spec = importlib.util.spec_from_file_location("member_verification_run", _EXTRACT)
if _spec is None or _spec.loader is None:
    raise ImportError(f"Cannot load {_EXTRACT}")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    return int(_mod.main())


if __name__ == "__main__":
    raise SystemExit(main())

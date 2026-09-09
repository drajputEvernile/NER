"""Member verification for OCR JSON docs within a max page count.

Same queue + batch output as run.py, except OCR JSON docs with more than
MAX_PAGES pages are skipped in the queue.

Reads OCR JSON from Azure Blob (AZURE_OCR_STORAGE_WRITE_PREFIX).
Writes one batch folder under the single MV_OUTPUT_PATH root:
  {MV_OUTPUT_PATH}/{YYYYMMDD_HHMMSS}/member_verification_{model}.csv
  {MV_OUTPUT_PATH}/{YYYYMMDD_HHMMSS}/ner_{model}.csv

Usage (from repo root):
  .\\.venv\\Scripts\\python.exe Member_Verification\\run_selected.py
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "azure_blob"))
sys.path.insert(0, str(HERE))

_spec = importlib.util.spec_from_file_location("member_verification_run", HERE / "run.py")
if _spec is None or _spec.loader is None:
    raise ImportError(f"Cannot load {HERE / 'run.py'}")
mv_run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mv_run)

logger = logging.getLogger(__name__)


def run_selected() -> pd.DataFrame:
    return mv_run.run("selected")


def main() -> int:
    mv_run.setup_logging()
    frame = run_selected()
    print(frame.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

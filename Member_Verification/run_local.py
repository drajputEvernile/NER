"""Member verification over OCR JSON on disk instead of Azure Blob.

Everything else is the production pipeline: the same queue, rules, NER layer,
what-if rejection and batch CSV output. Only the OCR source is rebound, so
this is the way to exercise the application without a storage account.

OCR JSON is looked for under a local root, in either shape:

  {root}/{RecordId}/{sub}/{RecordId}.json     (Data/output layout)
  {root}/{RecordId}.json

Usage (from repo root):
  .\\.venv\\Scripts\\python.exe Member_Verification\\run_local.py
  .\\.venv\\Scripts\\python.exe Member_Verification\\run_local.py --root Data\\output
  .\\.venv\\Scripts\\python.exe Member_Verification\\run_local.py --records Test1,Test2
  .\\.venv\\Scripts\\python.exe Member_Verification\\run_local.py --mode selected
"""

from __future__ import annotations

import argparse
import importlib.util
import json
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

DEFAULT_ROOT = REPO_ROOT / "Data" / "output"
# Preferred first: Azure OCR output is closer to what the blob holds.
OCR_SUBDIRS = ("Azure_OCR_Output", "Docling_OCR_Output", "")


def document_path(root: Path, record_id: str) -> Path | None:
    """The OCR JSON for a record, or None when there is none on disk."""
    for sub in OCR_SUBDIRS:
        candidate = root / record_id / sub / f"{record_id}.json" if sub else root / record_id / f"{record_id}.json"
        if candidate.is_file():
            return candidate
    flat = root / f"{record_id}.json"
    return flat if flat.is_file() else None


def local_records(root: Path) -> list[str]:
    """Every record id that has OCR JSON under the root."""
    found: list[str] = []
    if not root.is_dir():
        return found
    for child in sorted(root.iterdir()):
        if child.is_dir() and document_path(root, child.name) is not None:
            found.append(child.name)
        elif child.is_file() and child.suffix.lower() == ".json":
            found.append(child.stem)
    return sorted(set(found))


def bind_local_source(root: Path, only: list[str] | None = None) -> None:
    """Point run.py's OCR seams at the local root."""
    records = local_records(root)
    if only:
        wanted = {name.strip() for name in only if name.strip()}
        missing = sorted(wanted - set(records))
        if missing:
            raise SystemExit(
                f"No OCR JSON under {root} for: {', '.join(missing)}"
            )
        records = [name for name in records if name in wanted]
    if not records:
        raise SystemExit(f"No OCR JSON found under {root}")

    def _source() -> tuple[str, str]:
        return "local", str(root)

    def _list() -> list[str]:
        return records

    def _load(record_id: str) -> tuple[dict | None, list[dict]]:
        path = document_path(root, record_id)
        if path is None:
            return None, []
        document = json.loads(path.read_text(encoding="utf-8-sig"))
        pages = list((document or {}).get("pages") or [])
        pages = sorted(pages, key=lambda page: int(page.get("pageNumber") or 0))
        logger.info("ocr source=local record=%s pages=%s path=%s", record_id, len(pages), path)
        return document, pages

    mv_run.ocr_source = _source
    mv_run.list_ocr_records = _list
    mv_run.load_ocr_document = _load


def run_local(root: Path, mode: str = "all", only: list[str] | None = None) -> pd.DataFrame:
    bind_local_source(root, only)
    return mv_run.run(mode)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="local OCR JSON root")
    parser.add_argument("--mode", default="all", choices=("all", "selected"))
    parser.add_argument("--records", default="", help="comma-separated record ids")
    args = parser.parse_args(argv)

    mv_run.setup_logging()
    only = args.records.split(",") if args.records else None
    frame = run_local(Path(args.root), args.mode, only)
    print(frame.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Member verification over local transcript TXT files instead of Azure Blob.

Everything else is the production pipeline: the same queue, rules, NER layer,
what-if rejection and batch CSV output. Only the OCR source is rebound, so
this is the way to exercise the application without a storage account.

TXT transcripts are read from ``{input}/{RecordId}/*.txt``. Lines such as
``===== 1.jpg =====`` split one transcript into separate pages. A flat
``{input}/{RecordId}.txt`` file is also supported.

Usage (from repo root):
    .\\.venv\\Scripts\\python.exe Member_Verification\\run_local.py
  .\\.venv\\Scripts\\python.exe Member_Verification\\run_local.py --records Test1,Test2
  .\\.venv\\Scripts\\python.exe Member_Verification\\run_local.py --mode selected
"""

from __future__ import annotations

import argparse
import importlib.util
import logging
import re
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

LOCAL_INPUT_PATH = Path(r"E:\Projects\NER\Test_Input")
LOCAL_OUTPUT_PATH = Path(r"E:\Projects\NER\Test_Output")
LOCAL_SYSTEM_INPUT_PATH = Path(r"E:\Projects\NER\system_input.csv")
LOCAL_PROGRESS_PATH = LOCAL_OUTPUT_PATH / "mv_progress.json"
LOCAL_STAGING_PATH = LOCAL_OUTPUT_PATH / "mv_staging"
PAGE_BREAK = re.compile(r"^\s*={3,}\s+.+?\s+={3,}\s*$", re.MULTILINE)


def transcript_paths(root: Path, record_id: str) -> list[Path]:
    record_dir = root / record_id
    if record_dir.is_dir():
        return sorted(path for path in record_dir.glob("*.txt") if path.is_file())
    flat = root / f"{record_id}.txt"
    return [flat] if flat.is_file() else []


def transcript_pages(paths: list[Path]) -> list[str]:
    """Read TXT transcripts and split pages at marker lines."""
    pages: list[str] = []
    for path in paths:
        text = path.read_text(encoding="utf-8-sig")
        chunks = PAGE_BREAK.split(text) if PAGE_BREAK.search(text) else [text]
        pages.extend(chunk.strip() for chunk in chunks if chunk.strip())
    return pages


def local_records(root: Path) -> list[str]:
    """Every record id that has TXT transcripts under the root."""
    found: list[str] = []
    if not root.is_dir():
        return found
    for child in sorted(root.iterdir()):
        if child.is_dir() and transcript_paths(root, child.name):
            found.append(child.name)
        elif child.is_file() and child.suffix.lower() == ".txt":
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
        paths = transcript_paths(root, record_id)
        pages = [
            {"pageNumber": page_number, "content": text}
            for page_number, text in enumerate(transcript_pages(paths), start=1)
        ]
        logger.info("ocr source=local record=%s pages=%s path=%s", record_id, len(pages), root)
        return {"recordId": record_id, "pageCount": len(pages), "pages": pages}, pages

    mv_run.ocr_source = _source
    mv_run.list_ocr_records = _list
    mv_run.load_ocr_document = _load


def run_local(mode: str = "all", only: list[str] | None = None) -> pd.DataFrame:
    mv_run.mv_config.System_Input_path = LOCAL_SYSTEM_INPUT_PATH
    mv_run.mv_config.Output_path = LOCAL_OUTPUT_PATH
    mv_run.mv_config.PROGRESS_FILE = LOCAL_PROGRESS_PATH
    mv_run.mv_config.STAGING_DIR = LOCAL_STAGING_PATH
    bind_local_source(LOCAL_INPUT_PATH, only)
    return mv_run.run(mode)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", default="all", choices=("all", "selected"))
    parser.add_argument("--records", default="", help="comma-separated record ids")
    args = parser.parse_args(argv)

    mv_run.setup_logging()
    only = args.records.split(",") if args.records else None
    frame = run_local(args.mode, only)
    print(frame.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

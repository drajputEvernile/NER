"""Select Raw documents with <= Master_Data_Max_Pages pages for Master Data Builder.

Standalone: can be imported or run from CLI with -N.
Writes selected_records.json under config.Master_Data_Output.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
KV_ROOT = HERE.parent
if str(KV_ROOT) not in sys.path:
    sys.path.insert(0, str(KV_ROOT))

from Util import config

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


def count_pages(record_dir: Path) -> int:
    if not record_dir.is_dir():
        return 0
    return sum(1 for path in record_dir.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_EXTS)


def list_eligible_records(
    raw_input: Path | None = None,
    max_pages: int | None = None,
) -> list[dict]:
    """All record folders under Raw_Input with page_count <= max_pages."""
    root = Path(raw_input or config.Raw_Input)
    limit = int(max_pages if max_pages is not None else config.Master_Data_Max_Pages)
    rows: list[dict] = []
    if not root.is_dir():
        return rows
    for child in sorted(path for path in root.iterdir() if path.is_dir()):
        pages = count_pages(child)
        if pages <= 0 or pages > limit:
            continue
        rows.append(
            {
                "record_id": child.name,
                "path": str(child),
                "page_count": pages,
            }
        )
    return rows


def select_records(
    n: int,
    *,
    raw_input: Path | None = None,
    max_pages: int | None = None,
    output_dir: Path | None = None,
) -> dict:
    """Pick up to N eligible records and write selected_records.json.

    Returns the payload written to disk.
    """
    if n < 1:
        raise ValueError("N must be >= 1")
    eligible = list_eligible_records(raw_input=raw_input, max_pages=max_pages)
    chosen = eligible[:n]
    out_root = Path(output_dir or config.Master_Data_Output)
    out_root.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "n_requested": n,
        "n_selected": len(chosen),
        "max_pages": int(max_pages if max_pages is not None else config.Master_Data_Max_Pages),
        "raw_input": str(raw_input or config.Raw_Input),
        "records": chosen,
    }
    path = out_root / "selected_records.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    payload["path"] = str(path)
    return payload


def load_selected_records(output_dir: Path | None = None) -> dict:
    path = Path(output_dir or config.Master_Data_Output) / "selected_records.json"
    if not path.is_file():
        return {"records": [], "path": str(path)}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"records": [], "path": str(path)}
    if not isinstance(data, dict):
        return {"records": [], "path": str(path)}
    data["path"] = str(path)
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Select Raw records for Master Data Builder")
    parser.add_argument("-N", "--n", type=int, required=True, help="Number of records to select")
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help=f"Max pages (default config.Master_Data_Max_Pages={config.Master_Data_Max_Pages})",
    )
    args = parser.parse_args(argv)
    payload = select_records(args.n, max_pages=args.max_pages)
    print(
        f"selected {payload['n_selected']}/{payload['n_requested']} "
        f"(eligible scanned under {payload['raw_input']}) -> {payload['path']}"
    )
    for row in payload["records"]:
        print(f"  {row['record_id']}  pages={row['page_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

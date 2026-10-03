"""Convert a run's Excel workbook into one CSV file per sheet.

    .\\.venv\\Scripts\\python.exe convert_excel_to_csv.py <run folder> [<run folder> ...]
    .\\.venv\\Scripts\\python.exe convert_excel_to_csv.py <path to KV_Extraction.xlsx>
    .\\.venv\\Scripts\\python.exe convert_excel_to_csv.py --all             (every run under the Runs folder)
    .\\.venv\\Scripts\\python.exe convert_excel_to_csv.py --all --runs-dir C:\\...\\Output_Training\\Runs

For a run  {run}\\KV_Extraction.xlsx  it writes  {run}\\KV_Extraction\\{Sheet}.csv  (Member_Name.csv, Member_ID.csv,
Member_DOB.csv, Provider_Name.csv, E_Sign.csv, DOS.csv, Page_No.csv, Headings.csv, Overall.csv): the layout the
Extraction code and the Review UI now read and write.

Safe by design:
  * the Excel file is only read, never changed or deleted (keep it until you have checked the CSVs);
  * the CSVs are written to a scratch folder, read back, and compared with the Excel data cell by cell: the
    folder only gets its final name if every sheet is identical;
  * an existing KV_Extraction folder is never overwritten (that would replace reviews): it is skipped, unless
    --force, which moves it aside to KV_Extraction.replaced_<time> first.

Close the Review UI backend before converting a run you are reviewing (it holds the run in memory and would not
see the new files until it restarts), and do not open the workbook in Excel while this runs.

Needs only pandas and openpyxl (the repo venv has both). It does not import any repo code.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
XLSX = "KV_Extraction.xlsx"
FOLDER = "KV_Extraction"
ENCODING = "utf-8-sig"  # a byte-order mark, so Excel opens the CSVs with the right characters


def runs_dir_from_config() -> Path | None:
    config = ROOT / "Extraction" / "Util" / "config.py"
    try:
        text = config.read_text(encoding="utf-8")
    except OSError:
        return None
    found = re.search(r'^Output_Root\s*=\s*Path\(\s*r?["\']([^"\']+)["\']', text, re.MULTILINE)
    return Path(found.group(1)) / "Runs" if found else None


def convert(workbook: Path, force: bool) -> bool:
    run = workbook.parent
    target = run / FOLDER
    print(f"\n{run.name}")
    print(f"  workbook : {workbook}  ({workbook.stat().st_size / 1e6:.2f} MB)")
    if target.exists():
        if not force:
            print(f"  skipped  : {target.name}\\ already exists (use --force to move it aside and convert again)")
            return True
        aside = run / f"{FOLDER}.replaced_{datetime.now():%Y%m%d_%H%M%S}"
        target.rename(aside)
        print(f"  moved the existing folder to {aside.name}")

    started = time.perf_counter()
    sheets = pd.read_excel(workbook, sheet_name=None, dtype=str, keep_default_na=False)
    read_seconds = time.perf_counter() - started
    print(f"  read the workbook: {read_seconds:.1f} s, {len(sheets)} sheets")

    scratch = run / f"{FOLDER}.converting"
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir()
    started = time.perf_counter()
    try:
        for name, frame in sheets.items():
            frame.to_csv(scratch / f"{name}.csv", index=False, encoding=ENCODING, lineterminator="\n")
        write_seconds = time.perf_counter() - started
        # read every CSV back and compare it with what the workbook held
        problems = []
        for name, frame in sheets.items():
            back = pd.read_csv(scratch / f"{name}.csv", dtype=str, keep_default_na=False, encoding=ENCODING)
            same = list(back.columns) == list(frame.columns) and back.shape == frame.shape and back.equals(frame)
            size = (scratch / f"{name}.csv").stat().st_size / 1e6
            print(f"  {name:14s} {frame.shape[0]:7d} rows x {frame.shape[1]:3d} columns  {size:7.2f} MB   {'identical' if same else 'DIFFERENT'}")
            if not same:
                problems.append(name)
        if problems:
            print(f"  FAILED: {', '.join(problems)} did not read back identical; nothing was kept.")
            shutil.rmtree(scratch, ignore_errors=True)
            return False
        scratch.rename(target)
    except Exception as exc:  # noqa: BLE001
        print(f"  FAILED: {exc}")
        shutil.rmtree(scratch, ignore_errors=True)
        return False
    print(f"  wrote and verified {len(sheets)} CSV files in {write_seconds:.1f} s  ->  {target}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", help="run folders or KV_Extraction.xlsx files")
    parser.add_argument("--all", action="store_true", help="every run folder under the Runs folder that has a workbook")
    parser.add_argument("--runs-dir", help="the Runs folder (default: read from Extraction/Util/config.py)")
    parser.add_argument("--force", action="store_true", help="convert again even if the CSV folder exists (the old one is moved aside)")
    args = parser.parse_args()

    workbooks: list[Path] = []
    for item in args.paths:
        path = Path(item)
        workbooks.append(path if path.suffix.lower() == ".xlsx" else path / XLSX)
    if args.all:
        runs_dir = Path(args.runs_dir) if args.runs_dir else runs_dir_from_config()
        if not runs_dir or not runs_dir.is_dir():
            print("Could not find the Runs folder; give it with --runs-dir.")
            return 2
        workbooks += sorted(p / XLSX for p in runs_dir.glob("KV_Run_*") if (p / XLSX).is_file())
    if not workbooks:
        parser.print_help()
        return 2

    ok = True
    for workbook in workbooks:
        if not workbook.is_file():
            print(f"\n{workbook}: not found")
            ok = False
            continue
        ok = convert(workbook, args.force) and ok
    print("\nAll done." if ok else "\nSome runs were not converted (see above).")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

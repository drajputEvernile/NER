"""Check that the reviews are really saved in the run's CSV tables, and back them up.

Run it from the repo root with the repo venv (it only reads, and it is safe to run while the Review UI
backend and frontend are running; do NOT stop them first):

    .\\.venv\\Scripts\\python.exe check_review_tables.pyw
    .\\.venv\\Scripts\\python.exe check_review_tables.pyw --expect-kv 522 --expect-headings 330 --expect-pages 54 --expect-docs 15

What it does, in this order, and what each part tells you:

  1. FILES      the run's table files, and any leftover temp file: size, last write.
  2. LOCKS      which programs hold the table files open right now (Windows Restart Manager), and which process
                is the Review UI backend.
  3. DISK       how many reviews are actually inside the CSV files, per sheet, per document, and when the
                newest one was saved.
  4. BACKEND    asks the running backend (read-only) what it holds in memory: reviewed pages/documents, whether
                it is still saving, and the error it is showing.
  5. BACKUP     reads every review out of the backend's memory and writes review_backup_<run>_<time>.json
                (read-only calls; an independent copy of your reviews).
  6. COMPARE    memory vs the files, field by field: anything that exists only in memory is NOT on disk.
  7. SPEED      how long the tables take to read and write on this machine, whether a write + replace in the
                run folder works at all, and how fast the backend answers.
  8. VERDICT    whether it is safe to close the backend.

Everything is also written to review_check_report_<time>.txt. Send that text back.

It never writes to the tables. The only files it creates are the report, the backup, and scratch files
(__review_check_*) in the run folder that it removes again.

With --restore backup.json --run KV_Run_x it re-applies a backup to a running backend (asks first).

This file is .pyw (not .py) on purpose: the backend is started with --reload, which restarts it, and loses unsaved
reviews, whenever a .py file in the repo changes.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import platform
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
KV_SHEETS = ["Member_Name", "Member_ID", "Member_DOB", "Provider_Name", "E_Sign", "DOS", "Page_No"]
HEADINGS = "Headings"
FIELD_OF_SHEET = {
    "Member_Name": "name", "Member_ID": "member_id", "Member_DOB": "dob", "Provider_Name": "provider_name",
    "E_Sign": "electronic_signature", "DOS": "dos", "Page_No": "page_no", "Headings": "heading_heron",
}
SHEET_OF_FIELD = {field: sheet for sheet, field in FIELD_OF_SHEET.items()}
TABLES = "KV_Extraction"
SCRATCH = "__review_check_"

REPORT: list[str] = []


def say(text: str = "") -> None:
    print(text, flush=True)
    REPORT.append(text)


def section(title: str) -> None:
    say()
    say("=" * 100)
    say(title)
    say("=" * 100)


def local_time(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")


def ago(epoch: float) -> str:
    seconds = max(0, time.time() - epoch)
    if seconds < 90:
        return f"{seconds:.0f} s ago"
    if seconds < 5400:
        return f"{seconds / 60:.0f} min ago"
    return f"{seconds / 3600:.1f} h ago"


# ---------------------------------------------------------------- finding things


def runs_dir_from_config() -> Path | None:
    """Run_Output = Output_Root / Runs, from the literal path in Extraction/Util/config.py."""
    config = ROOT / "Extraction" / "Util" / "config.py"
    try:
        text = config.read_text(encoding="utf-8")
    except OSError:
        return None
    found = re.search(r'^Output_Root\s*=\s*Path\(\s*r?["\']([^"\']+)["\']', text, re.MULTILINE)
    return Path(found.group(1)) / "Runs" if found else None


def listening_pid(port: int) -> int | None:
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[3] == "LISTENING" and parts[1].endswith(f":{port}"):
            return int(parts[4])
    return None


def process_name(pid: int) -> str:
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=20
        ).stdout.strip()
        return out.split('","')[0].strip('"') if out and out.startswith('"') else "?"
    except (OSError, subprocess.SubprocessError):
        return "?"


def who_locks(paths: list[Path]) -> list[tuple[int, str]] | None:
    """Programs that have any of the files open right now (Windows Restart Manager); None if it cannot be asked."""
    if os.name != "nt":
        return None
    try:
        from ctypes import wintypes

        class FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

        class RM_UNIQUE_PROCESS(ctypes.Structure):
            _fields_ = [("dwProcessId", wintypes.DWORD), ("ProcessStartTime", FILETIME)]

        class RM_PROCESS_INFO(ctypes.Structure):
            _fields_ = [
                ("Process", RM_UNIQUE_PROCESS),
                ("strAppName", ctypes.c_wchar * 256),
                ("strServiceShortName", ctypes.c_wchar * 64),
                ("ApplicationType", ctypes.c_int),
                ("AppStatus", wintypes.ULONG),
                ("TSSessionId", wintypes.DWORD),
                ("bRestartable", wintypes.BOOL),
            ]

        rm = ctypes.WinDLL("rstrtmgr")
        session = wintypes.DWORD(0)
        key = ctypes.create_unicode_buffer(33)
        if rm.RmStartSession(ctypes.byref(session), 0, key) != 0:
            return None
        try:
            files = (ctypes.c_wchar_p * len(paths))(*[str(path) for path in paths])
            if rm.RmRegisterResources(session, len(paths), files, 0, None, 0, None) != 0:
                return None
            needed, have, reasons = wintypes.UINT(0), wintypes.UINT(0), wintypes.DWORD(0)
            result = rm.RmGetList(session, ctypes.byref(needed), ctypes.byref(have), None, ctypes.byref(reasons))
            if result == 0 and needed.value == 0:
                return []
            if result != 234:  # ERROR_MORE_DATA
                return None
            info = (RM_PROCESS_INFO * needed.value)()
            have = wintypes.UINT(needed.value)
            if rm.RmGetList(session, ctypes.byref(needed), ctypes.byref(have), info, ctypes.byref(reasons)) != 0:
                return None
            return [(item.Process.dwProcessId, item.strAppName) for item in info[: have.value]]
        finally:
            rm.RmEndSession(session)
    except Exception:  # noqa: BLE001 - a diagnostic must not crash on an exotic Windows setup
        return None


# ---------------------------------------------------------------- the backend (read-only)


class Api:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.latencies: dict[str, list[float]] = {}

    def get(self, path: str, params: dict[str, str] | None = None, timeout: float = 180.0):
        url = self.base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        started = time.perf_counter()
        with urllib.request.urlopen(url, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        label = "run detail" if re.fullmatch(r"/api/runs/[^/]+", path) else (path.split("/")[-1] or path)
        self.latencies.setdefault(label, []).append(time.perf_counter() - started)
        return data

    def post(self, path: str, body: dict, timeout: float = 300.0):
        request = urllib.request.Request(
            self.base + path, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST"
        )
        started = time.perf_counter()
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        self.latencies.setdefault("POST " + path.split("/")[-1], []).append(time.perf_counter() - started)
        return data


# ---------------------------------------------------------------- reading the tables


def read_copy(tables: Path) -> tuple[dict, float, Path]:
    """Copy the tables aside (so the real files are open only for an instant) and read the copies."""
    import pandas as pd

    scratch = Path(tempfile.mkdtemp(prefix="review_check_"))
    copy = scratch / TABLES
    shutil.copytree(tables, copy, ignore=shutil.ignore_patterns("*.tmp"))
    started = time.perf_counter()
    sheets = {
        path.stem: pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        for path in sorted(copy.glob("*.csv"))
    }
    return sheets, time.perf_counter() - started, copy


def count_disk(sheets: dict) -> dict:
    """What reviews the file holds: per sheet and in total."""
    out: dict = {"sheets": {}, "page_fields": set(), "pages": set(), "stamps": []}
    page_fields_per_page: dict[tuple, set] = {}
    for name in [*KV_SHEETS, HEADINGS]:
        frame = sheets.get(name)
        if frame is None:
            out["sheets"][name] = None
            continue
        if "reviewed_at" not in frame.columns:
            out["sheets"][name] = {"rows": len(frame), "no_review_columns": True}
            continue
        stamped = frame[frame["reviewed_at"] != ""]
        info = {
            "rows": len(frame),
            "stamped_rows": len(stamped),
            "right": int((frame["accuracy"] == "right").sum()),
            "wrong": int((frame["accuracy"] == "wrong").sum()),
            "missed": int((frame["accuracy"] == "missed").sum()),
            "fixed": int((frame["accuracy"] == "fixed").sum()),
            "review_rows": int((frame["source"] == "review").sum()),
        }
        keys = stamped[["record_id", "page_number", "file_name"]].drop_duplicates()
        info["page_fields"] = len(keys)
        for record, page, file in keys.itertuples(index=False, name=None):
            out["page_fields"].add((record, page, file, FIELD_OF_SHEET[name]))
            out["pages"].add((record, page, file))
            page_fields_per_page.setdefault((record, page, file), set()).add(name)
        out["stamps"] += [value for value in stamped["reviewed_at"] if value]
        out["sheets"][name] = info
    out["fully_reviewed_pages"] = {key for key, names in page_fields_per_page.items() if len(names) == len(KV_SHEETS) + 1}
    return out


def parse_stamp(text: str) -> float | None:
    try:
        return datetime.fromisoformat(text).astimezone().timestamp()
    except ValueError:
        return None


# ---------------------------------------------------------------- the checks


def check_files(run_dir: Path) -> tuple[Path, list[Path]]:
    section("1. FILES in " + str(run_dir))
    tables = run_dir / TABLES
    listing = [*run_dir.glob("*"), *(tables.glob("*") if tables.is_dir() else [])]
    for path in sorted(listing, key=lambda item: str(item).lower()):
        if path.is_file():
            stat = path.stat()
            say(f"  {str(path.relative_to(run_dir)):42s} {stat.st_size / 1e6:9.2f} MB   written {local_time(stat.st_mtime)} ({ago(stat.st_mtime)})")
    say()
    say(f"  tables folder exists: {tables.is_dir()}")
    leftovers = sorted(tables.glob("*.tmp")) if tables.is_dir() else []
    if leftovers:
        for tmp in leftovers:
            stat = tmp.stat()
            say(f"  LEFTOVER temp file: {tmp.name} ({stat.st_size / 1e6:.2f} MB, written {ago(stat.st_mtime)}). A save was started and did not finish.")
    else:
        say("  no leftover temp files")
    return tables, leftovers


def check_locks(paths: list[Path], backend_port: int) -> int | None:
    section("2. WHO HAS THE FILES OPEN (a lock on a table file is what blocks a save)")
    backend_pid = listening_pid(backend_port)
    if backend_pid:
        say(f"  Review UI backend: port {backend_port} is served by PID {backend_pid} ({process_name(backend_pid)})")
    else:
        say(f"  Nothing is listening on port {backend_port}: the backend is NOT running (or runs on another port).")
    existing = [path for path in paths if path.exists()]
    holders = who_locks(existing) if existing else []
    if holders is None:
        say("  (could not ask Windows who holds the files)")
    elif not holders:
        say("  right now no program has a table file or a temp file open")
    else:
        for pid, name in holders:
            mark = "  <- the Review UI backend itself" if pid == backend_pid else ""
            say(f"  OPEN: PID {pid}  {name}{mark}")
    return backend_pid


def check_disk(run_dir: Path, tables: Path, args) -> tuple[dict, dict]:
    section("3. WHAT IS INSIDE THE CSV FILES (read from copies; the real files are not touched)")
    if not tables.is_dir():
        say("  The tables folder does not exist.")
        return {}, {}
    sheets, read_seconds, copy = read_copy(tables)
    disk = count_disk(sheets)
    say(f"  read all tables in {read_seconds:.1f} s")
    say()
    say(f"  {'sheet':14s} {'rows':>7s} {'judged right':>13s} {'judged wrong':>13s} {'missed':>7s} {'fixed':>6s} {'reviewed page-fields':>21s}")
    totals = {"kv_judged": 0, "head_judged": 0, "kv_added": 0, "head_added": 0}
    for name in [*KV_SHEETS, HEADINGS]:
        info = disk["sheets"].get(name)
        if info is None:
            say(f"  {name:14s} SHEET MISSING")
            continue
        if info.get("no_review_columns"):
            say(f"  {name:14s} {info['rows']:7d}   (no review columns: these tables are from an older layout)")
            continue
        judged = info["right"] + info["wrong"]
        added = info["missed"] + info["fixed"]
        totals["head_judged" if name == HEADINGS else "kv_judged"] += judged
        totals["head_added" if name == HEADINGS else "kv_added"] += added
        say(f"  {name:14s} {info['rows']:7d} {info['right']:13d} {info['wrong']:13d} {info['missed']:7d} {info['fixed']:6d} {info['page_fields']:21d}")
    say()
    say(f"  KV cases judged (right + wrong) on disk ..... {totals['kv_judged']}   (+ {totals['kv_added']} missed/fixed values you added)")
    say(f"  Heading cases judged (right + wrong) on disk . {totals['head_judged']}   (+ {totals['head_added']} missed/fixed headings you added)")
    documents = {record for record, _, _ in disk["pages"]}
    say(f"  pages with at least one reviewed field ... {len(disk['pages'])}   in {len(documents)} documents")
    say(f"  pages with ALL 8 fields reviewed ......... {len(disk['fully_reviewed_pages'])}")
    stamps = [t for t in (parse_stamp(s) for s in disk["stamps"]) if t]
    if stamps:
        newest_file = max(path.stat().st_mtime for path in tables.glob("*.csv"))
        say(f"  oldest review on disk: {local_time(min(stamps))}")
        say(f"  NEWEST review on disk: {local_time(max(stamps))}  ({ago(max(stamps))});  the tables were last written {local_time(newest_file)} ({ago(newest_file)})")
    else:
        say("  the tables contain NO reviews at all")

    overall = sheets.get("Overall")
    if overall is not None and len(overall):
        say()
        say("  per document (pages in the run / pages with any review / pages fully reviewed):")
        pages_in_run = overall.groupby("RecordId").size()
        for record, count in pages_in_run.items():
            touched = sum(1 for r, _, _ in disk["pages"] if r == record)
            full = sum(1 for r, _, _ in disk["fully_reviewed_pages"] if r == record)
            say(f"    {record:28s} {count:4d} / {touched:4d} / {full:4d}")
    disk["totals"] = totals
    disk["read_seconds"] = read_seconds
    disk["copy"] = copy
    disk["sheets_frames"] = sheets
    return disk, totals


def official_scores(disk: dict, run_name: str):
    """The same scoring the Review UI shows, computed from the file on disk (needs the repo's code)."""
    extraction = ROOT / "Extraction"
    if not (extraction / "Training" / "labels.py").is_file() or "copy" not in disk:
        return None
    sys.path.insert(0, str(extraction))
    try:
        from Training import labels  # type: ignore

        scratch = Path(tempfile.mkdtemp(prefix="review_check_score_"))
        run_dir = scratch / run_name
        run_dir.mkdir()
        shutil.copytree(disk["copy"], run_dir / labels.config.Tables_Folder)
        pooled = labels.pooled_labels([run_dir])
        return labels.evaluate_all(run_dir, pooled)
    except Exception as exc:  # noqa: BLE001
        return f"could not compute: {exc}"


def check_backend(api: Api, run_id: str, disk: dict):
    section("4. WHAT THE RUNNING BACKEND HOLDS IN MEMORY")
    try:
        api.get("/api/health", timeout=20)
    except Exception as exc:  # noqa: BLE001
        say(f"  The backend does not answer ({exc}).")
        say("  If it was stopped, everything that was not already saved to disk is gone; the files on disk are all that is left.")
        return None
    runs = api.get("/api/runs")
    say(f"  runs the backend lists: {len(runs['runs'])}")
    say(f"  {'run':26s} {'model':6s} {'docs':>5s} {'pages':>6s} {'reviewed pages':>15s} {'reviewed docs':>14s}")
    for item in runs["runs"]:
        mark = "  <- checked" if item["id"] == run_id else ""
        say(f"  {item['id']:26s} {item['model_version']:6s} {item['total_documents']:5d} {item['total_pages']:6d} {item['reviewed_pages']:15d} {item['reviewed_documents']:14d}{mark}")
    detail = api.get(f"/api/runs/{run_id}")
    say()
    say(f"  backend, run {run_id}:")
    say(f"    reviewed pages / pages (all 8 fields) .. {detail['reviewed_pages']} / {detail['total_pages']}   in {detail['reviewed_documents']} documents")
    say(f"    KV     right/wrong/missed .............. {detail['kv']['right']} / {detail['kv']['wrong']} / {detail['kv']['missed']}")
    say(f"    Heading right/wrong/missed .............. {detail['heading']['right']} / {detail['heading']['wrong']} / {detail['heading']['missed']}")
    say(f"    still has unsaved changes (saving) ...... {detail['saving']}")
    say(f"    save error it is showing ................ {detail['save_error'] or '(none)'}")
    return detail


def backup_from_api(api: Api, run_id: str, detail: dict) -> dict:
    section("5. BACKUP: every review the backend holds, read out of its memory (read-only calls)")
    entries: list[dict] = []
    pages = [(doc["record_id"], page) for doc in detail["documents"] for page in doc["pages"]]
    started = time.time()
    for n, (record, page) in enumerate(pages, 1):
        data = api.get(
            f"/api/runs/{urllib.parse.quote(run_id)}/page",
            {"record_id": page["record_id"], "page_number": page["page_number"], "file_name": page["file_name"]},
        )
        for field in data["fields"]:
            if field["review"]:
                entries.append(
                    {
                        "record_id": page["record_id"], "page_number": page["page_number"], "file_name": page["file_name"],
                        "field": field["id"], "review": field["review"],
                        "candidates_seen": [
                            {"candidate_id": c["candidate_id"], "key": c["key_text"] or c["key"], "value": c["value"], "selected": c["selected"]}
                            for c in field["candidates"]
                        ],
                    }
                )
        if n % 10 == 0 or n == len(pages):
            say(f"  read {n}/{len(pages)} pages ({time.time() - started:.0f} s), {len(entries)} reviewed page-fields so far")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = Path.cwd() / f"review_backup_{run_id}_{stamp}.json"
    path.write_text(
        json.dumps({"run_id": run_id, "created_at": datetime.now().isoformat(timespec="seconds"), "reviews": entries}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    say(f"  wrote {path}  ({path.stat().st_size / 1e6:.2f} MB)")
    return {"path": path, "entries": entries}


def memory_counts(entries: list[dict]) -> dict:
    per_field: dict[str, dict] = {}
    for entry in entries:
        slot = per_field.setdefault(entry["field"], {"page_fields": 0, "judged": 0, "added": 0})
        slot["page_fields"] += 1
        slot["judged"] += len(entry["review"].get("candidates") or {})
        slot["added"] += len(entry["review"].get("added") or [])
    return per_field


def compare(disk: dict, entries: list[dict]) -> bool:
    section("6. MEMORY (backend) vs THE CSV FILES")
    memory = memory_counts(entries)
    disk_keys = disk.get("page_fields", set())
    mem_keys = {(e["record_id"], e["page_number"], e["file_name"], e["field"]) for e in entries}
    say(f"  {'sheet':14s} {'in memory':>10s} {'on disk':>12s} {'only in memory':>15s}   | judged cases: memory vs disk")
    only_memory = 0
    for name in [*KV_SHEETS, HEADING_FIELD_SHEET]:
        field = FIELD_OF_SHEET[name]
        mem = memory.get(field, {"page_fields": 0, "judged": 0, "added": 0})
        disk_info = (disk.get("sheets") or {}).get(name) or {}
        in_file = {key for key in disk_keys if key[3] == field}
        missing = {key for key in mem_keys if key[3] == field} - in_file
        only_memory += len(missing)
        say(f"  {name:14s} {mem['page_fields']:10d} {len(in_file):12d} {len(missing):15d}   | {mem['judged']:5d} vs {(disk_info.get('right', 0) + disk_info.get('wrong', 0)):5d}")
    mem_kv = sum(v["judged"] for k, v in memory.items() if not k.startswith("heading_"))
    mem_head = sum(v["judged"] for k, v in memory.items() if k.startswith("heading_"))
    say()
    say(f"  KV cases judged:      memory {mem_kv}   disk {disk.get('totals', {}).get('kv_judged')}")
    say(f"  Heading cases judged: memory {mem_head}   disk {disk.get('totals', {}).get('head_judged')}")
    say(f"  page-fields reviewed ONLY in the backend's memory (not on disk): {only_memory}")
    return only_memory == 0


HEADING_FIELD_SHEET = HEADINGS


def check_speed(run_dir: Path, tables: Path, disk: dict, api: Api | None) -> dict:
    section("7. SPEED: how long a save takes on this machine")
    result: dict = {}
    # (a) can a temp file be written and replaced in this folder at all
    test_tmp, test_final = run_dir / (SCRATCH + "write_test.tmp"), run_dir / (SCRATCH + "write_test.csv")
    try:
        started = time.perf_counter()
        test_tmp.write_bytes(os.urandom(1_000_000))
        os.replace(test_tmp, test_final)
        say(f"  write 1 MB + replace in the run folder: OK ({(time.perf_counter() - started) * 1000:.0f} ms)")
        result["write_replace_ok"] = True
    except OSError as exc:
        say(f"  write 1 MB + replace in the run folder FAILED: {exc}")
        say("  -> something in this folder blocks the write-then-replace pattern (antivirus / sync / permissions).")
        result["write_replace_ok"] = False
    finally:
        for path in (test_tmp, test_final):
            path.unlink(missing_ok=True)
    # (b) how long every table takes to write (the most one save can ever cost) and to read back
    frames = disk.get("sheets_frames")
    if frames and not ARGS.no_bench:
        bench = run_dir / (SCRATCH + "benchmark")
        try:
            shutil.rmtree(bench, ignore_errors=True)
            bench.mkdir()
            started = time.perf_counter()
            cells = 0
            slowest = ("", 0.0)
            for title, frame in frames.items():
                t = time.perf_counter()
                frame.to_csv(bench / f"{title}.csv", index=False, encoding="utf-8-sig", lineterminator="\n")
                took = time.perf_counter() - t
                cells += frame.shape[0] * frame.shape[1]
                if took > slowest[1]:
                    slowest = (title, took)
            total = time.perf_counter() - started
            say(f"  writing EVERY table: {total:.2f} s  ({cells / 1e6:.2f} million cells); the biggest, {slowest[0]}, takes {slowest[1]:.2f} s")
            say(f"  a save rewrites only the sheet it changed, so one save costs at most {slowest[1]:.2f} s on this machine")
            say(f"  reading every table back: {disk['read_seconds']:.2f} s")
            result["write_seconds"], result["cells"] = total, cells
        except Exception as exc:  # noqa: BLE001
            say(f"  could not benchmark the write: {exc}")
        finally:
            shutil.rmtree(bench, ignore_errors=True)
    elif ARGS.no_bench:
        say("  (benchmark skipped: --no-bench)")
    # (c) how fast the backend answers read-only requests
    if api is not None and api.latencies:
        say()
        say("  backend answer times for the read-only requests made above (seconds):")
        for name, values in api.latencies.items():
            say(f"    {name:12s} n={len(values):3d}   median {statistics.median(values):6.2f}   slowest {max(values):6.2f}")
    return result


def verdict(files: dict, only_memory_ok: bool | None, detail: dict | None, disk: dict, speed: dict) -> None:
    section("8. VERDICT")
    if detail is None:
        say("  The backend is not reachable, so memory could not be compared.")
        say("  What is on disk (section 3) is what you have.")
        return
    saving = detail["saving"] or bool(detail["save_error"])
    if only_memory_ok is None:
        if saving:
            say(f"  NOT SAFE YET: the backend still has unsaved changes / a save error: {detail['save_error'] or 'saving...'}")
            say("  Do not close the backend. Run this again without --no-backup to get the backup and the field-by-field proof.")
        else:
            say("  The backend reports nothing left to save and no error, but this run skipped the comparison (--no-backup).")
            say("  Run it once without --no-backup: only that comparison proves the files hold everything.")
        return
    if only_memory_ok and not saving:
        say("  SAFE: every review the backend holds is on disk, and the backend has nothing left to save.")
        say("        You can close the backend and frontend.")
        return
    say("  NOT SAFE TO CLOSE THE BACKEND YET.")
    if not only_memory_ok:
        say("  - some reviews exist only in the backend's memory (section 6). If it is closed or restarted they are lost.")
    if saving:
        say(f"  - the backend still has unsaved changes / a save error: {detail['save_error'] or 'saving...'}")
    say("  - the backup file written in section 5 holds all of those reviews; keep it. It can be re-applied with --restore.")
    say("  - do not edit any .py file in the repo, do not git pull, and do not close the backend until this says SAFE.")


# ---------------------------------------------------------------- restore


def restore(api: Api, backup_file: Path, run_id: str) -> None:
    data = json.loads(backup_file.read_text(encoding="utf-8"))
    entries = data["reviews"]
    say(f"Backup {backup_file.name}: {len(entries)} reviewed page-fields from run {data['run_id']}.")
    say(f"Target: {api.base}  run {run_id}. Existing reviews of those page-fields in that run will be replaced.")
    if input("Type YES to apply: ").strip() != "YES":
        say("Cancelled.")
        return
    ok = failed = 0
    for n, entry in enumerate(entries, 1):
        review = entry["review"]
        body = {
            "record_id": entry["record_id"], "page_number": entry["page_number"], "file_name": entry["file_name"],
            "field": entry["field"], "reviewer": review.get("reviewer", ""), "not_present": bool(review.get("not_present")),
            "candidates": {
                cid: {k: v.get(k, "") for k in ("verdict", "reason", "belongs_to", "level")}
                for cid, v in (review.get("candidates") or {}).items()
            },
            "added": [
                {"value_words": a.get("value_words") or [], "key_words": a.get("key_words") or [],
                 "for_candidate": a.get("for_candidate", ""), "level": a.get("level", "")}
                for a in (review.get("added") or [])
            ],
        }
        try:
            api.post(f"/api/runs/{urllib.parse.quote(run_id)}/review", body)
            ok += 1
        except urllib.error.HTTPError as exc:
            failed += 1
            say(f"  FAILED {entry['record_id']} {entry['file_name']} {entry['field']}: {exc.read().decode('utf-8', 'replace')[:200]}")
        if n % 25 == 0 or n == len(entries):
            say(f"  applied {ok}, failed {failed} of {n}/{len(entries)}")
    say(f"Done: {ok} applied, {failed} failed.")


# ---------------------------------------------------------------- main

ARGS: argparse.Namespace


def main() -> int:
    global ARGS
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", help="run folder name (KV_Run_...) or its full path; default: the run with the most reviews in the backend, else the newest")
    parser.add_argument("--runs-dir", help="the Runs folder (default: read from Extraction/Util/config.py)")
    parser.add_argument("--api", default="http://127.0.0.1:3000", help="the Review UI backend (default %(default)s)")
    parser.add_argument("--expect-kv", type=int, help="the number of KV cases you reviewed (for comparison)")
    parser.add_argument("--expect-headings", type=int, help="the number of heading cases you reviewed")
    parser.add_argument("--expect-pages", type=int, help="the number of pages you reviewed")
    parser.add_argument("--expect-docs", type=int, help="the number of documents you reviewed")
    parser.add_argument("--no-backup", action="store_true", help="do not write the backup JSON")
    parser.add_argument("--no-bench", action="store_true", help="do not benchmark writing the tables")
    parser.add_argument("--restore", metavar="BACKUP_JSON", help="re-apply a backup to the running backend (needs --run)")
    ARGS = parser.parse_args()
    api = Api(ARGS.api)

    if ARGS.restore:
        if not ARGS.run:
            parser.error("--restore needs --run KV_Run_...")
        restore(api, Path(ARGS.restore), Path(ARGS.run).name)
        return 0

    say(f"review tables check   {datetime.now():%Y-%m-%d %H:%M:%S}   {platform.node()}   Python {platform.python_version()}   {platform.system()} {platform.release()}")
    port = int(urllib.parse.urlparse(ARGS.api).port or 3000)

    # which run
    runs_dir = Path(ARGS.runs_dir) if ARGS.runs_dir else runs_dir_from_config()
    chosen: Path | None = None
    if ARGS.run and Path(ARGS.run).is_dir() and (Path(ARGS.run) / TABLES).is_dir():
        chosen = Path(ARGS.run).resolve()
    elif runs_dir and runs_dir.is_dir():
        if ARGS.run:
            chosen = runs_dir / ARGS.run
        else:
            candidates = sorted((p for p in runs_dir.glob("KV_Run_*") if (p / TABLES / "Overall.csv").is_file()), key=lambda p: p.name)
            try:
                listed = {r["id"]: r["reviewed_pages"] for r in api.get("/api/runs", timeout=60)["runs"]}
                best = max(candidates, key=lambda p: (listed.get(p.name, 0), p.name), default=None)
            except Exception:  # noqa: BLE001
                best = candidates[-1] if candidates else None
            chosen = best
    if chosen is None or not chosen.is_dir():
        say("Could not find the run. Give it with --run KV_Run_... and/or --runs-dir <the Runs folder>.")
        return 2
    say(f"checking run: {chosen.name}   (folder {chosen})")

    tables, leftovers = check_files(chosen)
    backend_pid = check_locks([*sorted(tables.glob("*.csv")), *leftovers] if tables.is_dir() else [], port)
    disk, totals = check_disk(chosen, tables, ARGS)

    score = official_scores(disk, chosen.name) if disk else None
    if isinstance(score, dict):
        say()
        say("  scored the way the Review UI scores it (from the files): "
            f"KV {score['kv']['accuracy']}% ({score['kv']['correct']} right, {score['kv']['wrong']} wrong, {score['kv']['missed']} missed); "
            f"Headings {score['heading']['accuracy']}% ({score['heading']['correct']} right, {score['heading']['wrong']} wrong, {score['heading']['missed']} missed)")
    elif isinstance(score, str):
        say("  " + score)

    detail = check_backend(api, chosen.name, disk)
    only_memory_ok: bool | None = None
    backup = None
    if detail is not None and not ARGS.no_backup:
        backup = backup_from_api(api, chosen.name, detail)
        only_memory_ok = compare(disk or {}, backup["entries"])
    elif detail is not None:
        say("  (backup skipped: --no-backup)")

    if any(v is not None for v in (ARGS.expect_kv, ARGS.expect_headings, ARGS.expect_pages, ARGS.expect_docs)):
        section("YOUR NUMBERS vs WHAT WAS FOUND")
        mem = memory_counts(backup["entries"]) if backup else {}
        mem_kv = sum(v["judged"] for k, v in mem.items() if not k.startswith("heading_")) if backup else None
        mem_head = sum(v["judged"] for k, v in mem.items() if k.startswith("heading_")) if backup else None
        pages_disk = len(disk.get("fully_reviewed_pages", [])) if disk else None
        docs_disk = len({r for r, _, _ in disk.get("pages", [])}) if disk else None
        def pairs(item):
            return item["correct"] + item["wrong"] + item["missed"]

        ui_kv_mem = detail["kv"]["right"] + detail["kv"]["wrong"] + detail["kv"]["missed"] if detail else None
        ui_head_mem = detail["heading"]["right"] + detail["heading"]["wrong"] + detail["heading"]["missed"] if detail else None
        ui_kv_disk = pairs(score["kv"]) if isinstance(score, dict) else None
        ui_head_disk = pairs(score["heading"]) if isinstance(score, dict) else None
        say("  'KV cases' / 'heading cases' as the UI top bar counts them are right + wrong + missed:")
        say(f"    KV      : backend memory {ui_kv_mem}   disk {ui_kv_disk}")
        say(f"    Headings: backend memory {ui_head_mem}   disk {ui_head_disk}")
        say("    (the backend scores with the reviews of every run on the same OCR, the disk number only with this run, so they can differ slightly)")
        say("  and as raw judged rows (candidates you ticked or crossed):")
        for label, mine, mem_value, disk_value in (
            ("KV cases", ARGS.expect_kv, mem_kv, totals.get("kv_judged") if totals else None),
            ("heading cases", ARGS.expect_headings, mem_head, totals.get("head_judged") if totals else None),
            ("pages (fully reviewed)", ARGS.expect_pages, detail["reviewed_pages"] if detail else None, pages_disk),
            ("documents", ARGS.expect_docs, detail["reviewed_documents"] if detail else None, docs_disk),
        ):
            if mine is not None:
                say(f"    {label:24s} you counted {mine:5d}   backend memory: {mem_value}   disk: {disk_value}")

    speed = check_speed(chosen, tables, disk or {}, api if detail is not None else None)
    verdict({}, only_memory_ok, detail, disk or {}, speed)

    report = Path.cwd() / f"review_check_report_{datetime.now():%Y%m%d_%H%M%S}.txt"
    report.write_text("\n".join(REPORT) + "\n", encoding="utf-8")
    print(f"\nreport written to {report}\n(send that text back; the backup JSON stays with you)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)

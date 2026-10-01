"""Move fully reviewed records from the review system to the training system, through copy-paste.

Nothing is cut off or dropped: any file over MAX_PART_CHARS is split into numbered part files and
merged back byte-for-byte (every part and the merged file are checked with SHA-256).

Step 1, on the review system:   python training_data_builder.py export --run KV_Run_YYYYMMDD_HHMMSS [--records A B ...]
                                    (add --list to only show review status; --run is then optional)
    Training Data/<RecordId>/
        Raw/                      the page images
        data/
            manifest.json         every data file: size, SHA-256 and its part files
            record.json           record id, pages, OCR file name, export time
            ocr/<name>.json       the OCR JSON (written as .partNNNofMMM.txt files when over the limit)
            annotations/labels.json   latest review of every page and field, all batches pooled
            annotations/truth.csv     the final true value(s) per page and field, flat
            candidates/<run>.csv      the candidate log of the exported batch(es)
            runs.json             model version / time of those batches
            accuracy.csv          reviewed accuracy per batch and model version, per field
    Training Data/accuracy_by_version.csv   the same, pooled over every exported record

Step 2: copy the folders of Training Data to the training system by hand. Raw images are copied as
files; the part files of data/ are pasted into the chat, one file each, under the same names.

Step 3, on the training system: python training_data_builder.py merge [--clean-parts]
    Puts every data file back together, checks it, and lists what is still missing.

Step 4, on the training system: python training_data_builder.py install [--overwrite]
    Copies the merged records into the pipeline: images to RAW_INPUT, OCR to OCR_INPUT, and the
    candidate logs, run.json and pooled labels into RUN_OUTPUT as KV_Run_* batches, so the Review
    UI, Training/dataset.py, train.py and evaluate.py see them like any other batch.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ------------------------------------------------------------------ settings (edit these)

# Where page images, OCR JSON and batch folders live on the machine this runs on.
RAW_INPUT = Path(r"E:\Projects\NER\Data\Raw")
OCR_INPUT = Path(r"E:\Projects\NER\Data\OCR_Output")
RUN_OUTPUT = Path(r"E:\Projects\NER\Data\Output\Runs")

# Where export writes (and merge / install read) the records.
TRAINING_DATA = Path(r"E:\Projects\NER\NER\Training Data")

# Every file and part file stays under this many characters (a paste that works reliably is 40000).
MAX_PART_CHARS = 40000

# ------------------------------------------------------------------ helpers

CODE_DIR = Path(__file__).resolve().parent / "KV_Extraction"
RUN_PREFIX = "KV_Run_"
IMAGE_FOLDER = "Raw"
DATA_FOLDER = "data"
MANIFEST = "manifest.json"
SUMMARY_FILE = "accuracy_by_version.csv"
ACCURACY_COLUMNS = ["run_id", "model_version", "field", "correct", "wrong", "missed", "total", "accuracy", "page_fields"]
TRUTH_COLUMNS = ["record_id", "page_number", "file_name", "field", "not_present", "value", "value2", "value_norm",
                 "key", "level", "id_type", "value_words", "key_words", "source"]


def labels_module():
    """Training.labels (imports the KV config, which needs the repo's Python 3.13 venv)."""
    sys.path.insert(0, str(CODE_DIR))
    from Training import labels

    return labels


def canonical(text: str) -> str:
    """Text as it is hashed, split and stored: LF newlines, no leading / trailing whitespace, so a
    paste that adds or drops a final newline cannot change it."""
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compact_json(value: Any) -> str:
    """Lossless, ASCII-only JSON on one line (safe to paste, no whitespace at the edges)."""
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def csv_text(columns: list[str], rows: list[dict[str, Any]]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=columns, lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def split_text(text: str) -> list[str]:
    """Pieces of at most MAX_PART_CHARS that join back to text. Cuts never sit next to whitespace,
    so a part keeps its exact edges when pasted."""
    if len(text) <= MAX_PART_CHARS:
        return [text]
    parts: list[str] = []
    start = 0
    while len(text) - start > MAX_PART_CHARS:
        cut = start + MAX_PART_CHARS
        while cut > start + 1 and (text[cut - 1].isspace() or text[cut].isspace()):
            cut -= 1
        parts.append(text[start:cut])
        start = cut
    parts.append(text[start:])
    return parts


def part_name(name: str, number: int, total: int) -> str:
    width = max(3, len(str(total)))
    return f"{name}.part{number:0{width}d}of{total:0{width}d}.txt"


def write_data_file(data_dir: Path, rel: str, text: str, manifest: dict[str, Any]) -> None:
    """Write data/<rel>, or its part files when it is over the limit, and list it in the manifest."""
    text = canonical(text)
    target = data_dir / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    pieces = split_text(text)
    entry: dict[str, Any] = {"chars": len(text), "sha256": sha256(text), "parts": []}
    if len(pieces) == 1:
        target.write_text(text + "\n", encoding="utf-8", newline="\n")
    else:
        for number, piece in enumerate(pieces, 1):
            name = part_name(target.name, number, len(pieces))
            (target.parent / name).write_text(piece, encoding="utf-8", newline="\n")
            entry["parts"].append({"file": name, "chars": len(piece), "sha256": sha256(piece)})
    manifest["files"][rel] = entry


def runs_newest_first() -> list[Path]:
    if not RUN_OUTPUT.is_dir():
        return []
    return sorted((p for p in RUN_OUTPUT.iterdir() if p.is_dir() and p.name.startswith(RUN_PREFIX)),
                  key=lambda p: p.name, reverse=True)


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def run_meta(run_dir: Path) -> dict[str, Any]:
    meta = read_json(run_dir / "run.json")
    return meta if isinstance(meta, dict) else {}


def ocr_json_path(record_id: str) -> Path | None:
    folder = OCR_INPUT / record_id
    jsons = [p for p in folder.glob("*.json") if p.is_file()] if folder.is_dir() else []
    return max(jsons, key=lambda p: p.stat().st_size) if jsons else None


# ------------------------------------------------------------------ export


def record_pages(L, runs: list[Path]) -> dict[str, dict[str, dict[str, str]]]:
    """record -> page key -> {page_number, file_name, ocr_sha1} from the candidate logs
    (the newest batch that has the page decides its ocr_sha1)."""
    records: dict[str, dict[str, dict[str, str]]] = {}
    for run_dir in runs:  # newest first
        for log in L.candidate_logs(run_dir):
            frame = L.record_log(run_dir, log.stem)
            for row in frame[["record_id", "page_number", "file_name", "ocr_sha1"]].drop_duplicates().to_dict("records"):
                key = L.page_key(row["record_id"], row["page_number"], row["file_name"])
                records.setdefault(log.stem, {}).setdefault(key, {
                    "page_number": row["page_number"], "file_name": row["file_name"], "ocr_sha1": row["ocr_sha1"]})
    return records


def review_status(L, runs: list[Path], pooled, records) -> dict[str, dict[str, Any]]:
    """Per record: pages, fully reviewed pages, and the OCR pages no batch has a log for."""
    best: dict[str, int] = {}
    for run_dir in runs:
        for key, count in L.reviewed_fields(run_dir, pooled).items():
            best[key] = max(best.get(key, 0), count)
    status: dict[str, dict[str, Any]] = {}
    for record_id, pages in records.items():
        ocr = ocr_json_path(record_id)
        data = read_json(ocr) if ocr else None
        ocr_files = {str(p.get("fileName") or "") for p in (data or {}).get("pages", [])} if isinstance(data, dict) else set()
        logged = {page["file_name"] for page in pages.values()}
        done = sum(1 for key in pages if best.get(key, 0) >= len(L.FIELDS))
        unlogged = sorted(name for name in ocr_files if name not in logged and name.casefold() not in {n.casefold() for n in logged})
        status[record_id] = {
            "pages": len(pages), "reviewed": done, "ocr_pages": len(ocr_files), "unlogged": unlogged,
            "has_ocr": ocr is not None,
            "complete": bool(pages) and done == len(pages) and not unlogged and ocr is not None,
        }
    return status


def consolidated_labels(L, runs: list[Path], record_id: str, pages: dict[str, dict[str, str]]) -> dict[str, Any]:
    """The latest label of every page-field over all batches, one labels.json (schema of a batch).
    Labels made on a different OCR text than the page's current one are left out, as the
    accuracy code does."""
    out: dict[str, Any] = {}
    for run_dir in runs:
        if not L.labels_path(run_dir).is_file():
            continue
        for key, entry in L.load_labels(run_dir)["pages"].items():
            if key not in pages:
                continue
            sha = pages[key]["ocr_sha1"]
            if entry.get("ocr_sha1") and sha and entry["ocr_sha1"] != sha:
                continue
            page = out.setdefault(key, {"record_id": record_id, "page_number": pages[key]["page_number"],
                                        "file_name": pages[key]["file_name"], "fields": {}, "ocr_sha1": sha})
            for field, label in entry.get("fields", {}).items():
                current = page["fields"].get(field)
                if current is None or label.get("reviewed_at", "") > current.get("reviewed_at", ""):
                    page["fields"][field] = label
            info = entry.get("page_info")
            if info and info.get("saved_at", "") > (page.get("page_info") or {}).get("saved_at", ""):
                page["page_info"] = info
    return {"schema_version": L.SCHEMA_VERSION, "pages": out}


def truth_rows(labels: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for page in labels["pages"].values():
        for field, label in page["fields"].items():
            base = {"record_id": page["record_id"], "page_number": page["page_number"],
                    "file_name": page["file_name"], "field": field, "not_present": int(bool(label.get("not_present")))}
            truth = label.get("truth") or []
            if not truth:
                rows.append({**base, "source": ""})
            for item in truth:
                rows.append({**base, **{k: item.get(k, "") for k in ("value", "value2", "value_norm", "key", "level", "id_type", "source")},
                             "value_words": " ".join(map(str, item.get("value_words") or [])),
                             "key_words": " ".join(map(str, item.get("key_words") or []))})
    return rows


def accuracy_rows(L, runs: list[Path], record_id: str, pooled) -> list[dict[str, Any]]:
    """Accuracy of each batch of this record against the pooled reviews, per field and overall."""
    mine = {key: label for key, label in pooled.items() if key[0].startswith(record_id + "|")}
    rows: list[dict[str, Any]] = []
    for run_dir in sorted(runs, key=lambda p: p.name):
        frame = L.record_log(run_dir, record_id)
        if frame.empty:
            continue
        score = L.evaluate_log(frame, mine)
        version = run_meta(run_dir).get("model_version") or "v0"
        for field, item in {"ALL": score, **score["fields"]}.items():
            rows.append({"run_id": run_dir.name, "model_version": version, "field": field, "correct": item["correct"],
                         "wrong": item["wrong"], "missed": item["missed"], "total": item["total"],
                         "accuracy": "" if item["accuracy"] is None else item["accuracy"], "page_fields": item["page_fields"]})
    return rows


def write_summary(rows_by_record: dict[str, list[dict[str, Any]]]) -> Path:
    """accuracy_by_version.csv: per model version and field, the newest batch of each record."""
    latest: dict[tuple[str, str], str] = {}
    for record_id, rows in rows_by_record.items():
        for row in rows:
            key = (record_id, row["model_version"])
            latest[key] = max(latest.get(key, ""), str(row["run_id"]))
    sums: dict[tuple[str, str], dict[str, int]] = {}
    records: dict[str, set[str]] = {}
    for record_id, rows in rows_by_record.items():
        for row in rows:
            if str(row["run_id"]) != latest[(record_id, row["model_version"])]:
                continue
            slot = sums.setdefault((row["model_version"], row["field"]), {"correct": 0, "wrong": 0, "missed": 0, "total": 0})
            for name in slot:
                slot[name] += int(row[name] or 0)
            records.setdefault(row["model_version"], set()).add(record_id)
    order = lambda item: (item[0][0], item[0][1] != "ALL", item[0][1])  # noqa: E731
    out = [{"model_version": version, "field": field, **slot,
            "accuracy": round(100 * slot["correct"] / slot["total"], 2) if slot["total"] else "",
            "records": len(records[version])} for (version, field), slot in sorted(sums.items(), key=order)]
    path = TRAINING_DATA / SUMMARY_FILE
    path.write_text(csv_text(["model_version", "field", "correct", "wrong", "missed", "total", "accuracy", "records"], out),
                    encoding="utf-8", newline="\n")
    return path


def export_record(L, runs: list[Path], selected: list[Path], pooled, record_id: str,
                  pages: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    """runs: every batch (reviews may sit in an earlier batch); selected: the batches being exported."""
    folder = TRAINING_DATA / record_id
    data_dir = folder / DATA_FOLDER
    manifest: dict[str, Any] = {"record_id": record_id, "max_part_chars": MAX_PART_CHARS, "files": {}}

    missing = []
    for page in pages.values():
        source = RAW_INPUT / record_id / page["file_name"]
        if source.is_file():
            (folder / IMAGE_FOLDER).mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, folder / IMAGE_FOLDER / page["file_name"])
        else:
            missing.append(page["file_name"])

    ocr = ocr_json_path(record_id)
    assert ocr is not None
    write_data_file(data_dir, f"ocr/{ocr.name}", compact_json(json.loads(ocr.read_text(encoding="utf-8-sig"))), manifest)

    labels = consolidated_labels(L, runs, record_id, pages)  # pooled over every batch, like the UI
    write_data_file(data_dir, "annotations/labels.json", compact_json(labels), manifest)
    write_data_file(data_dir, "annotations/truth.csv", csv_text(TRUTH_COLUMNS, truth_rows(labels)), manifest)

    # Reruns repeat the same candidates, so one candidate log per model version (its newest batch)
    # is enough; accuracy.csv still covers every batch.
    run_info = []
    seen_versions: set[str] = set()
    for run_dir in selected:  # newest first
        log = run_dir / "candidates" / f"{record_id}.csv"
        meta = run_meta(run_dir)
        if not log.is_file() or (meta.get("model_version") or "v0") in seen_versions:
            continue
        seen_versions.add(meta.get("model_version") or "v0")
        write_data_file(data_dir, f"candidates/{run_dir.name}.csv", log.read_text(encoding="utf-8-sig"), manifest)
        run_info.append({"run_id": run_dir.name, "model_version": meta.get("model_version") or "v0",
                         "ner_model_name": meta.get("ner_model_name") or "", "catalog_hash": meta.get("catalog_hash") or "",
                         "start_time": meta.get("start_time") or "", "source_run": meta.get("source_run") or ""})
    run_info.reverse()
    write_data_file(data_dir, "runs.json", compact_json(run_info), manifest)

    rows = accuracy_rows(L, selected, record_id, pooled)
    write_data_file(data_dir, "accuracy.csv", csv_text(ACCURACY_COLUMNS, rows), manifest)
    write_data_file(data_dir, "record.json", compact_json({
        "record_id": record_id, "ocr_file": ocr.name, "pages": list(pages.values()), "images_missing": missing,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}), manifest)

    (data_dir / MANIFEST).write_text(json.dumps(manifest, indent=1), encoding="utf-8", newline="\n")
    parts = sum(len(entry["parts"]) for entry in manifest["files"].values())
    print(f"  {record_id}: {len(pages)} pages, {len(manifest['files'])} data files, {parts} part files to paste"
          + (f", {len(missing)} images NOT FOUND" if missing else ""))
    return rows


def export(args: argparse.Namespace) -> None:
    L = labels_module()
    runs = runs_newest_first()
    if not runs:
        raise SystemExit(f"No {RUN_PREFIX}* batches under {RUN_OUTPUT}")
    if not args.list and not args.run:
        raise SystemExit("Give the batch to export: --run KV_Run_YYYYMMDD_HHMMSS (see --list)")
    names = {(n if n.startswith(RUN_PREFIX) else RUN_PREFIX + n).casefold() for n in args.run or []}
    selected = [r for r in runs if not names or r.name.casefold() in names]
    unknown = names - {r.name.casefold() for r in selected}
    if unknown:
        raise SystemExit("Batch not found under " + str(RUN_OUTPUT) + ": " + ", ".join(sorted(unknown)))
    pooled = L.pooled_labels(runs)  # reviews of a batch may have been made in another batch
    records = record_pages(L, selected)
    status = review_status(L, runs, pooled, records)

    print(f"{'record':<24}{'pages':>6}{'reviewed':>10}  state")
    for record_id, item in sorted(status.items()):
        state = "complete" if item["complete"] else ("no OCR json" if not item["has_ocr"] else
                "OCR pages without a batch log: " + ", ".join(item["unlogged"][:3]) if item["unlogged"] else "incomplete")
        print(f"{record_id:<24}{item['pages']:>6}{item['reviewed']:>10}  {state}")
    if args.list:
        return

    wanted = {r.casefold() for r in args.records} if args.records else None
    chosen = [r for r, item in sorted(status.items()) if item["complete"] and (wanted is None or r.casefold() in wanted)]
    skipped = [r for r in (args.records or []) if r.casefold() not in {c.casefold() for c in chosen}]
    if skipped:
        print("Not exported (not complete or unknown): " + ", ".join(skipped))
    if not chosen:
        raise SystemExit("No completely reviewed records to export.")

    print(f"\nExporting {len(chosen)} records to {TRAINING_DATA}")
    rows_by_record = {record_id: export_record(L, runs, selected, pooled, record_id, records[record_id]) for record_id in chosen}
    summary = write_summary(rows_by_record)
    print(f"\nAccuracy over all versions: {summary}")
    print("Next: copy the Training Data folders to the training system (images as files, the .partNNNofMMM.txt "
          "files by paste under the same names), then run 'merge' there.")


# ------------------------------------------------------------------ merge


def merge_record(folder: Path, clean_parts: bool) -> tuple[bool, list[str]]:
    """Rebuild every data file of one record; return (all ok, problems)."""
    data_dir = folder / DATA_FOLDER
    manifest = read_json(data_dir / MANIFEST)
    if not isinstance(manifest, dict):
        return False, [f"{MANIFEST} is missing or unreadable"]
    problems: list[str] = []
    for rel, entry in manifest["files"].items():
        target = data_dir / rel
        parts = entry["parts"]
        if not parts:
            if not target.is_file():
                problems.append(f"{rel}: file missing")
            elif sha256(canonical(target.read_text(encoding="utf-8"))) != entry["sha256"]:
                problems.append(f"{rel}: checksum mismatch (file changed while copying)")
            continue
        pieces: list[str] = []
        bad = False
        for part in parts:
            path = target.parent / part["file"]
            if not path.is_file():
                problems.append(f"{rel}: missing {part['file']}")
                bad = True
                continue
            text = canonical(path.read_text(encoding="utf-8"))
            if len(text) != part["chars"] or sha256(text) != part["sha256"]:
                problems.append(f"{rel}: {part['file']} is damaged (has {len(text)} chars, expected {part['chars']}); paste it again")
                bad = True
            pieces.append(text)
        if bad:
            continue
        whole = "".join(pieces)
        if sha256(whole) != entry["sha256"]:
            problems.append(f"{rel}: parts do not join to the original (checksum mismatch)")
            continue
        target.write_text(whole + "\n", encoding="utf-8", newline="\n")
        if clean_parts:
            for part in parts:
                (target.parent / part["file"]).unlink(missing_ok=True)
    return not problems, problems


def merge(args: argparse.Namespace) -> None:
    folders = sorted(p for p in TRAINING_DATA.iterdir() if p.is_dir() and (p / DATA_FOLDER / MANIFEST).is_file()) if TRAINING_DATA.is_dir() else []
    if not folders:
        raise SystemExit(f"No records with data/{MANIFEST} under {TRAINING_DATA}")
    accuracy: dict[str, list[dict[str, Any]]] = {}
    complete = 0
    for folder in folders:
        ok, problems = merge_record(folder, args.clean_parts)
        images = len(list((folder / IMAGE_FOLDER).glob("*"))) if (folder / IMAGE_FOLDER).is_dir() else 0
        record = read_json(folder / DATA_FOLDER / "record.json")
        expected = len((record or {}).get("pages", [])) if isinstance(record, dict) else 0
        if expected and images < expected:
            problems.append(f"Raw: {images} of {expected} page images present")
        print(f"{'OK     ' if not problems else 'PROBLEM'} {folder.name}")
        for line in problems:
            print(f"         - {line}")
        if ok:
            complete += 1
            path = folder / DATA_FOLDER / "accuracy.csv"
            accuracy[folder.name] = [{**row} for row in read_csv(path)] if path.is_file() else []
    if accuracy:
        print(f"\nAccuracy over all versions: {write_summary(accuracy)}")
    print(f"{complete} of {len(folders)} records merged and verified.")


# ------------------------------------------------------------------ install


def install(args: argparse.Namespace) -> None:
    folders = sorted(p for p in TRAINING_DATA.iterdir() if p.is_dir() and (p / DATA_FOLDER / "record.json").is_file()) if TRAINING_DATA.is_dir() else []
    touched: set[Path] = set()
    for folder in folders:
        data_dir = folder / DATA_FOLDER
        manifest = read_json(data_dir / MANIFEST) or {}
        if any(not (data_dir / rel).is_file() for rel in manifest.get("files", {})):
            print(f"SKIP {folder.name}: run 'merge' first, data files are not all merged")
            continue
        record = read_json(data_dir / "record.json")
        record_id = record["record_id"]
        added = Path(data_dir / "ocr" / record["ocr_file"])

        raw_out = RAW_INPUT / record_id
        raw_out.mkdir(parents=True, exist_ok=True)
        for image in (folder / IMAGE_FOLDER).glob("*"):
            if args.overwrite or not (raw_out / image.name).exists():
                shutil.copy2(image, raw_out / image.name)

        ocr_out = OCR_INPUT / record_id / record["ocr_file"]
        if ocr_out.exists() and not args.overwrite:
            print(f"  {record_id}: OCR json already in {ocr_out.parent}, kept (use --overwrite to replace)")
        else:
            ocr_out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(added, ocr_out)

        runs = read_json(data_dir / "runs.json") or []
        for item in runs:
            run_dir = RUN_OUTPUT / item["run_id"]
            (run_dir / "candidates").mkdir(parents=True, exist_ok=True)
            log = run_dir / "candidates" / f"{record_id}.csv"
            if args.overwrite or not log.exists():
                shutil.copy2(data_dir / "candidates" / f"{item['run_id']}.csv", log)
            if not (run_dir / "run.json").exists():
                (run_dir / "run.json").write_text(json.dumps({
                    "version": "one_pass_v1", "run_id": item["run_id"].removeprefix(RUN_PREFIX), "status": "completed",
                    "imported": True, "model_version": item["model_version"], "ner_model_name": item["ner_model_name"],
                    "catalog_hash": item["catalog_hash"], "start_time": item["start_time"] or None,
                    "source_run": item["source_run"] or None, "run_folder": str(run_dir)}, indent=2), encoding="utf-8")
            touched.add(run_dir)

        # The pooled labels go into the newest batch of the record; pooling reads every batch.
        if runs:
            newest = RUN_OUTPUT / max(runs, key=lambda item: item["run_id"])["run_id"]
            target = newest / "review" / "labels.json"
            current = read_json(target) or {"schema_version": 2, "pages": {}}
            incoming = read_json(data_dir / "annotations" / "labels.json")["pages"]
            for key, page in incoming.items():
                slot = current["pages"].setdefault(key, {**{k: v for k, v in page.items() if k != "fields"}, "fields": {}})
                for field, label in page["fields"].items():
                    old = slot["fields"].get(field)
                    if old is None or args.overwrite or label.get("reviewed_at", "") > old.get("reviewed_at", ""):
                        slot["fields"][field] = label
                if page.get("page_info"):
                    slot["page_info"] = page["page_info"]
            current["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  {record_id}: installed ({len(runs)} batches)")

    for run_dir in touched:  # refresh the counts the Review UI lists
        meta = read_json(run_dir / "run.json") or {}
        if not meta.get("imported"):
            continue
        logs = sorted((run_dir / "candidates").glob("*.csv"))
        pages = {(log.stem, row["page_number"], row["file_name"]) for log in logs for row in read_csv(log)}
        meta.update(total_docs=len(logs), completed_documents=len(logs), total_pages=len(pages), completed_pages=len(pages))
        (run_dir / "run.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Done. {len(touched)} batches updated under {RUN_OUTPUT}")


# ------------------------------------------------------------------ main


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_export = sub.add_parser("export", help="review system: build Training Data from completely reviewed records")
    p_export.add_argument("--run", nargs="+", help="batch(es) to export, e.g. KV_Run_20260928_120748 (the prefix is optional)")
    p_export.add_argument("--list", action="store_true", help="only show which records are completely reviewed")
    p_export.add_argument("--records", nargs="+", help="export only these record ids")
    p_export.set_defaults(func=export)
    p_merge = sub.add_parser("merge", help="training system: rebuild the split data files and verify them")
    p_merge.add_argument("--clean-parts", action="store_true", help="delete the part files after a verified merge")
    p_merge.set_defaults(func=merge)
    p_install = sub.add_parser("install", help="training system: copy merged records into the pipeline folders")
    p_install.add_argument("--overwrite", action="store_true", help="replace files that already exist")
    p_install.set_defaults(func=install)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

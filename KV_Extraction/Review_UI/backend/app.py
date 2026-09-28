"""Local API for the KV extraction Review UI.

Batches are the KV_Run_* folders under config.Run_Output. Reviews are stored per run in
{run}/review/labels.json (see Training/labels.py) and exported to {run}/review/manual_review.xlsx.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
KV_ROOT = HERE.parent.parent
if str(KV_ROOT) not in sys.path:
    sys.path.insert(0, str(KV_ROOT))

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from Training import labels, registry
from Training.ner_export import run_started
from Training.ocr import page_lines
from Util import config

config.make_output_folders()

app = FastAPI(title="KV Extraction Review")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

RUN_PREFIX = "KV_Run_"
QUEUE_NAME = "kv_run_queue.json"
# Overlay folder per image tab; older runs used the short extractor prefixes.
IMAGE_KINDS = {
    "dob": "dob",
    "member_id": "member_id",
    "name": "name",
    "provider_name": "provider_name",
    "electronic_signature": "electronic_signature",
    "dos": "dos",
    "page_no": "page_no",
    **{name: name for name in labels.HEADING_FIELDS},
    "ID": "member_id",
    "MName": "name",
    "PName": "provider_name",
    "ESig": "electronic_signature",
}

_rerun_procs: list[subprocess.Popen] = []
_eval_cache: dict[str, tuple[tuple, dict[str, Any]]] = {}
_summary_cache: dict[str, tuple[float, dict[str, Any]]] = {}


# ---------------------------------------------------------------- runs


def _run_dirs() -> list[Path]:
    root = Path(config.Run_Output)
    if not root.is_dir():
        return []
    return sorted(
        (path for path in root.iterdir() if path.is_dir() and path.name.startswith(RUN_PREFIX)),
        key=run_started,
        reverse=True,
    )


def _resolve_run_dir(run_id: str) -> Path:
    if not run_id.startswith(RUN_PREFIX) or "/" in run_id or "\\" in run_id or ".." in run_id:
        raise HTTPException(404, "run not found")
    path = Path(config.Run_Output) / run_id
    if not path.is_dir():
        raise HTTPException(404, "run not found")
    return path


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _run_meta(run_dir: Path) -> dict[str, Any]:
    """run.json, else the (active or archived) queue of this run, else {}."""
    meta = _read_json(run_dir / "run.json")
    if meta:
        return meta
    run_id = run_dir.name[len(RUN_PREFIX):]
    root = Path(config.Run_Output)
    for path in [root / QUEUE_NAME, *sorted(root.glob(f"kv_run_queue_{run_id}*.json"))]:
        data = _read_json(path) if path.is_file() else None
        if data and data.get("run_id") == run_id:
            return data
    return {}


def _workbook_summary(run_dir: Path) -> dict[str, Any]:
    """Document / page / time totals from extraction.xlsx, for runs without run.json."""
    path = run_dir / "extraction.xlsx"
    if not path.is_file():
        return {}
    mtime = path.stat().st_mtime
    cached = _summary_cache.get(run_dir.name)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        frame = pd.read_excel(path, sheet_name="Extraction Summary", dtype=str).fillna("")
        pages = pd.to_numeric(frame.get("PageCount"), errors="coerce").fillna(0)
        seconds = pd.to_numeric(frame.get("TimeSeconds"), errors="coerce").fillna(0)
        summary = {
            "records": frame["RecordId"].tolist() if "RecordId" in frame else [],
            "pages": int(pages.sum()),
            "time": float(seconds.sum()),
        }
    except Exception:  # noqa: BLE001 - unreadable workbook: show the run without totals
        summary = {}
    _summary_cache[run_dir.name] = (mtime, summary)
    return summary


def _pid_alive(pid: Any) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _start_from_name(run_dir: Path) -> str | None:
    try:
        return datetime.strptime(run_dir.name[len(RUN_PREFIX):], "%Y%m%d_%H%M%S").astimezone().isoformat()
    except ValueError:
        return None


def _page_index(run_dir: Path) -> list[dict[str, str]]:
    """Distinct (record, page, file) in the run's candidate log."""
    frame = labels.run_log(run_dir)
    if frame.empty:
        return []
    pages = frame[["record_id", "page_number", "file_name"]].drop_duplicates()
    return pages.to_dict("records")


def _evaluation(run_dir: Path, all_runs: list[Path]) -> dict[str, Any]:
    """This run's accuracy against the latest reviews of its pages from any run (cached)."""
    logs = labels.candidate_logs(run_dir)
    signature = (
        tuple((path.name, path.stat().st_mtime) for path in logs),
        tuple(path.name for path in all_runs),
        labels.labels_signature(all_runs),
    )
    cached = _eval_cache.get(run_dir.name)
    if cached and cached[0] == signature:
        return cached[1]
    pooled = labels.pooled_labels(all_runs)
    result = labels.evaluate_all(run_dir, pooled)
    _eval_cache[run_dir.name] = (signature, result)
    return result


def _run_info(run_dir: Path, all_runs: list[Path]) -> dict[str, Any]:
    meta = _run_meta(run_dir)
    reviewable = bool(labels.candidate_logs(run_dir))
    summary = {} if meta else _workbook_summary(run_dir)

    status = str(meta.get("status") or ("completed" if (run_dir / "extraction.xlsx").is_file() else "unknown"))
    if status == "running" and not _pid_alive(meta.get("pid")):
        status = "stopped"

    documents = meta.get("documents") or []
    total_docs = int(meta.get("total_docs") or len(documents) or len(summary.get("records", [])))
    total_pages = int(meta.get("total_pages") or summary.get("pages") or 0)
    completed_docs = int(meta.get("completed_documents") or (total_docs if summary else 0))
    completed_pages = int(meta.get("completed_pages") or (total_pages if summary else 0))
    # Runs without run.json only have per-document extraction time from the workbook.
    total_time = float(meta.get("total_time_seconds") or summary.get("time") or 0.0)

    page_fields = 0
    reviewed_pages = 0
    if reviewable:
        per_page = labels.reviewed_fields(run_dir, labels.pooled_labels(all_runs))
        page_fields = sum(per_page.values())
        reviewed_pages = sum(1 for count in per_page.values() if count >= len(labels.FIELDS))
    score = _evaluation(run_dir, all_runs) if reviewable else None

    return {
        "id": run_dir.name,
        "status": status,
        "start_time": meta.get("start_time") or _start_from_name(run_dir),
        "end_time": meta.get("end_time"),
        "total_time_seconds": round(total_time, 1),
        "avg_time_per_page": round(total_time / completed_pages, 3) if completed_pages else None,
        "total_documents": total_docs,
        "total_pages": total_pages,
        "completed_documents": completed_docs,
        "completed_pages": completed_pages,
        "model_version": meta.get("model_version") or ("v0" if reviewable else "legacy"),
        "ner_model": meta.get("ner_model_name") or "",
        "source_run": meta.get("source_run"),
        "reviewable": reviewable,
        "reviewed_pages": reviewed_pages,
        "reviewed_page_fields": page_fields,
        "accuracy": score["accuracy"] if score else None,
        "accuracy_correct": score["correct"] if score else 0,
        "accuracy_wrong": score["wrong"] if score else 0,
        "accuracy_missed": score["missed"] if score else 0,
        "accuracy_total": score["total"] if score else 0,
        "kv_accuracy": score["kv"]["accuracy"] if score else None,
        "headings": score["headings"] if score else {},
    }


def _active_run(infos: list[dict[str, Any]]) -> str | None:
    _rerun_procs[:] = [proc for proc in _rerun_procs if proc.poll() is None]
    for info in infos:
        if info["status"] == "running":
            return info["id"]
    return "starting" if _rerun_procs else None


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/runs")
def list_runs() -> dict[str, Any]:
    dirs = _run_dirs()
    infos = [_run_info(path, dirs) for path in dirs]
    docs = sum(info["total_documents"] for info in infos)
    pages = sum(info["total_pages"] for info in infos)
    timed_pages = sum(info["completed_pages"] for info in infos if info["total_time_seconds"])
    total_time = sum(info["total_time_seconds"] for info in infos)
    correct = sum(info["accuracy_correct"] for info in infos)
    judged = sum(info["accuracy_total"] for info in infos)
    return {
        "runs": infos,
        "active_run": _active_run(infos),
        "totals": {
            "runs": len(infos),
            "documents": docs,
            "pages": pages,
            "avg_pages_per_document": round(pages / docs, 1) if docs else None,
            "avg_time_per_page": round(total_time / timed_pages, 3) if timed_pages else None,
            "accuracy": round(100 * correct / judged, 1) if judged else None,
            "accuracy_correct": correct,
            "accuracy_wrong": sum(info["accuracy_wrong"] for info in infos),
            "accuracy_missed": sum(info["accuracy_missed"] for info in infos),
            "accuracy_total": judged,
        },
    }


@app.get("/api/meta")
def meta() -> dict[str, Any]:
    return {
        "fields": [{"id": field, "label": label} for field, label in labels.FIELDS.items()],
        "reasons": [{"id": code, "label": label} for code, label in labels.REASONS.items()],
        "heading_reasons": [{"id": code, "label": label} for code, label in labels.HEADING_REASONS.items()],
        "correction_reasons": sorted(labels.CORRECTION_REASONS),
        "levels": list(labels.LEVELS),
        "id_types": [{"id": code, "label": label} for code, label in labels.ID_TYPES.items()],
    }


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    run_dir = _resolve_run_dir(run_id)
    dirs = _run_dirs()
    info = _run_info(run_dir, dirs)
    if not info["reviewable"]:
        raise HTTPException(409, f"{run_id} has no candidate log; rerun it to review.")
    per_page = labels.reviewed_fields(run_dir, labels.pooled_labels(dirs))
    order = [doc.get("record_id") for doc in _run_meta(run_dir).get("documents") or []]
    documents: dict[str, dict[str, Any]] = {record: None for record in order if record}  # type: ignore[misc]
    for page in _page_index(run_dir):
        record = page["record_id"]
        doc = documents.get(record)
        if doc is None:
            doc = documents[record] = {"record_id": record, "pages": []}
        key = labels.page_key(record, page["page_number"], page["file_name"])
        doc["pages"].append({**page, "reviewed_fields": per_page.get(key, 0)})
    out = []
    for doc in documents.values():
        if not doc:
            continue
        doc["pages"].sort(key=lambda item: int(item["page_number"]) if item["page_number"].isdigit() else 0)
        doc["page_count"] = len(doc["pages"])
        doc["reviewed_pages"] = sum(1 for p in doc["pages"] if p["reviewed_fields"] >= len(labels.FIELDS))
        out.append(doc)
    return {**info, "field_count": len(labels.FIELDS), "documents": out}


@app.get("/api/runs/{run_id}/page")
def get_page(run_id: str, record_id: str, page_number: str, file_name: str) -> dict[str, Any]:
    run_dir = _resolve_run_dir(run_id)
    found = labels.page_candidates(run_dir, record_id, page_number, file_name)
    key = labels.page_key(record_id, page_number, file_name)
    own = labels.load_labels(run_dir)["pages"].get(key, {}).get("fields", {})
    others = [path for path in _run_dirs() if path != run_dir]
    prior = labels.pooled_labels(others) if not all(field in own for field in labels.FIELDS) else {}
    fields = []
    for field, label in labels.FIELDS.items():
        previous = prior.get((key, field))
        if previous and previous.get("ocr_sha1") and previous["ocr_sha1"] != found[field]["ocr_sha1"]:
            previous = None
        if previous and field not in own:
            previous = labels.auto_review(found[field]["candidates"], previous, field in labels.HEADING_FIELDS)
        fields.append(
            {
                "id": field,
                "label": label,
                "kind": "heading" if field in labels.HEADING_FIELDS else "kv",
                "ocr_sha1": found[field]["ocr_sha1"],
                "candidates": found[field]["candidates"],
                "review": own.get(field),
                "prior": None if field in own else previous,
            }
        )
    info = labels.page_info(run_dir, record_id, page_number, file_name)
    return {
        "record_id": record_id,
        "page_number": page_number,
        "file_name": file_name,
        "fields": fields,
        "page_info": info,
        "page_info_prior": None if info else labels.pooled_page_info(others, key),
    }


@app.get("/api/runs/{run_id}/page-ocr")
def get_page_ocr(run_id: str, record_id: str, file_name: str) -> dict[str, Any]:
    _resolve_run_dir(run_id)
    return page_lines(record_id, file_name)


@app.get("/api/runs/{run_id}/image")
def get_image(run_id: str, record_id: str, file_name: str, kind: str = "overall") -> FileResponse:
    run_dir = _resolve_run_dir(run_id)
    paths: list[Path] = []
    if kind != "raw":
        folder_name = "overall" if kind == "overall" else IMAGE_KINDS.get(kind)
        if folder_name is None:
            raise HTTPException(400, f"unknown image kind: {kind}")
        folder = run_dir / "overlays" / folder_name / record_id
        if folder.is_dir():
            paths.extend(sorted(folder.glob(f"page_*_{file_name}")))
    paths.append(Path(config.Raw_Input) / record_id / file_name)
    for path in paths:
        if path.is_file():
            return FileResponse(path)
    raise HTTPException(404, f"image not found for {record_id}/{file_name} kind={kind}")


class Verdict(BaseModel):
    verdict: str = ""
    reason: str = ""
    belongs_to: str = ""
    prefer_candidate: str = ""
    level: str = ""
    id_type: str = ""
    block_before: str = ""
    block_after: str = ""


class AddedValue(BaseModel):
    value: str
    value2: str = ""
    key: str = ""
    for_candidate: str = ""
    level: str = ""
    id_type: str = ""
    value_words: list[int] = Field(default_factory=list)
    key_words: list[int] = Field(default_factory=list)


class ReviewBody(BaseModel):
    record_id: str
    page_number: str
    file_name: str
    field: str
    reviewer: str = ""
    not_present: bool = False
    candidates: dict[str, Verdict] = Field(default_factory=dict)
    added: list[AddedValue] = Field(default_factory=list)
    notes: str = ""


@app.post("/api/runs/{run_id}/review")
def save_review(run_id: str, body: ReviewBody) -> dict[str, Any]:
    run_dir = _resolve_run_dir(run_id)
    submission = body.model_dump()
    try:
        label = labels.save_field_review(
            run_dir, body.record_id, body.page_number, body.file_name, body.field, submission, body.reviewer
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    key = labels.page_key(body.record_id, body.page_number, body.file_name)
    return {"review": label, "reviewed_fields": labels.reviewed_fields(run_dir, labels.pooled_labels(_run_dirs())).get(key, 0)}


class PageInfoBody(BaseModel):
    record_id: str
    page_number: str
    file_name: str
    reviewer: str = ""
    page_sequence: str = ""


@app.post("/api/runs/{run_id}/page-info")
def save_page_info(run_id: str, body: PageInfoBody) -> dict[str, Any]:
    """Collected only: nothing detects or scores it."""
    run_dir = _resolve_run_dir(run_id)
    try:
        info = labels.save_page_info(
            run_dir, body.record_id, body.page_number, body.file_name, body.page_sequence, body.reviewer
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"page_info": info}


@app.delete("/api/runs/{run_id}/review")
def clear_review(run_id: str, record_id: str, page_number: str, file_name: str, field: str) -> dict[str, Any]:
    run_dir = _resolve_run_dir(run_id)
    labels.clear_field_review(run_dir, record_id, page_number, file_name, field)
    key = labels.page_key(record_id, page_number, file_name)
    return {"reviewed_fields": labels.reviewed_fields(run_dir, labels.pooled_labels(_run_dirs())).get(key, 0)}


# ---------------------------------------------------------------- versions and reruns


@app.get("/api/versions")
def versions() -> list[dict[str, Any]]:
    return registry.list_versions()


class RerunBody(BaseModel):
    model_version: str


@app.post("/api/runs/{run_id}/rerun")
def rerun(run_id: str, body: RerunBody) -> dict[str, Any]:
    run_dir = _resolve_run_dir(run_id)
    if not registry.is_runnable(body.model_version):
        raise HTTPException(400, f"Model version {body.model_version} cannot run yet.")
    dirs = _run_dirs()
    active = _active_run([_run_info(path, dirs) for path in dirs])
    if active:
        raise HTTPException(409, f"A run is already in progress ({active}).")

    config.make_output_folders()
    log_path = Path(config.Rerun_Logs) / f"{datetime.now():%Y%m%d_%H%M%S}_{run_id}_{body.model_version}.log"
    command = [
        sys.executable,
        str(KV_ROOT / "run.py"),
        "--fresh",
        "--records-from",
        run_dir.name,
        "--model-version",
        body.model_version,
    ]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            command, cwd=str(KV_ROOT.parent), stdout=log, stderr=subprocess.STDOUT, creationflags=flags
        )
    _rerun_procs.append(proc)
    # Fail fast on argument / data errors instead of reporting a run that never appears.
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and proc.poll() is None:
        time.sleep(0.2)
    if proc.poll() not in (None, 0):
        tail = log_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-8:]
        raise HTTPException(500, "Rerun failed to start:\n" + "\n".join(tail))
    return {"started": True, "pid": proc.pid, "log": str(log_path), "source_run": run_dir.name}


def _record_set(run_dir: Path) -> frozenset[str]:
    return frozenset(path.stem for path in labels.candidate_logs(run_dir))


@app.get("/api/runs/{run_id}/versions-accuracy")
def versions_accuracy(run_id: str) -> dict[str, Any]:
    """Accuracy of every run over this batch's documents, grouped by extraction model version.

    All runs are scored on the same labels: the latest review of each page from any run.
    """
    run_dir = _resolve_run_dir(run_id)
    records = _record_set(run_dir)
    if not records:
        raise HTTPException(409, f"{run_id} has no candidate log.")
    dirs = _run_dirs()
    known = {version["id"]: version for version in registry.list_versions()}
    groups: dict[str, list[dict[str, Any]]] = {}
    for path in dirs:
        if _record_set(path) != records:
            continue
        info = _run_info(path, dirs)
        score = _evaluation(path, dirs)
        groups.setdefault(info["model_version"], []).append(
            {
                "id": path.name,
                "start_time": info["start_time"],
                "status": info["status"],
                "ner_model": info["ner_model"],
                "accuracy": score["accuracy"],
                "correct": score["correct"],
                "wrong": score["wrong"],
                "missed": score["missed"],
                "total": score["total"],
                "fields": score["fields"],
                "kv": score["kv"],
                "headings": score["headings"],
            }
        )
    out = []
    for version, runs in groups.items():
        out.append(
            {
                "model_version": version,
                "label": known.get(version, {}).get("label", version),
                "latest": runs[0],
                "runs": runs,
            }
        )
    out.sort(key=lambda item: item["model_version"])
    return {
        "run_id": run_id,
        "documents": len(records),
        "fields": [{"id": field, "label": label} for field, label in labels.KV_FIELDS.items()],
        "heading_fields": [{"id": field, "label": label} for field, label in labels.HEADING_FIELDS.items()],
        "versions": out,
    }

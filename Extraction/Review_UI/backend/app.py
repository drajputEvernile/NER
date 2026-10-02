"""Local API for the Extraction Review UI.

Runs are the KV_Run_* folders under config.Run_Output that have a KV_Extraction.xlsx. Everything a
run extracted and everything a reviewer says about it lives in that one workbook (see
Training/labels.py); there is no other review store.
"""

from __future__ import annotations

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

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from Training import labels, registry
from Training.ner_export import run_started
from Training.ocr import page_lines
from Util import config

config.make_output_folders()

app = FastAPI(title="Extraction Review")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

RUN_PREFIX = "KV_Run_"
QUEUE_NAME = "kv_run_queue.json"

_rerun_procs: list[subprocess.Popen] = []
_eval_cache: dict[str, tuple[tuple, dict[str, Any]]] = {}


@app.on_event("shutdown")
def _save_reviews() -> None:
    labels.RunStore.flush_all()


# ---------------------------------------------------------------- runs


def _run_dirs() -> list[Path]:
    """Runs that have a workbook, newest first (runs from an older output layout are not listed)."""
    root = Path(config.Run_Output)
    if not root.is_dir():
        return []
    return sorted(
        (path for path in root.iterdir() if path.is_dir() and path.name.startswith(RUN_PREFIX) and labels.has_workbook(path)),
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
    import json

    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
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
    """Distinct (record, page, file) of the run, in page order."""
    frame = labels.run_log(run_dir)
    if frame.empty:
        return []
    pages = frame[["record_id", "page_number", "file_name"]].drop_duplicates()
    return pages.to_dict("records")


def _evaluation(run_dir: Path, all_runs: list[Path]) -> dict[str, Any]:
    """This run's accuracy against the latest reviews of its pages from any run (cached)."""
    signature = (tuple(path.name for path in all_runs), labels.labels_signature(all_runs))
    cached = _eval_cache.get(run_dir.name)
    if cached and cached[0] == signature:
        return cached[1]
    result = labels.evaluate_all(run_dir, labels.pooled_labels(all_runs))
    _eval_cache[run_dir.name] = (signature, result)
    return result


def _score(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "accuracy": item["accuracy"],
        "right": item["correct"],
        "wrong": item["wrong"],
        "missed": item["missed"],
    }


def _run_info(run_dir: Path, all_runs: list[Path]) -> dict[str, Any]:
    meta = _run_meta(run_dir)
    status = str(meta.get("status") or "completed")
    if status == "running" and not _pid_alive(meta.get("pid")):
        status = "stopped"

    documents = meta.get("documents") or []
    total_docs = int(meta.get("total_docs") or len(documents))
    total_pages = int(meta.get("total_pages") or 0)
    completed_pages = int(meta.get("completed_pages") or 0)

    pooled = labels.pooled_labels(all_runs)
    per_page = labels.reviewed_fields(run_dir, pooled)
    reviewed_pages = sum(1 for count in per_page.values() if count >= len(labels.FIELDS))
    reviewed_docs = len({key.split("|", 1)[0] for key, count in per_page.items() if count})
    score = _evaluation(run_dir, all_runs)
    store = labels.RunStore.get(run_dir)

    return {
        "id": run_dir.name,
        "status": status,
        "start_time": meta.get("start_time") or _start_from_name(run_dir),
        "end_time": meta.get("end_time"),
        "total_time_seconds": round(float(meta.get("total_time_seconds") or 0.0), 1),
        "avg_time_per_page": meta.get("avg_time_per_page"),
        "total_documents": total_docs,
        "total_pages": total_pages,
        "completed_documents": int(meta.get("completed_documents") or 0),
        "completed_pages": completed_pages,
        "model_version": meta.get("model_version") or "v0",
        "source_run": meta.get("source_run"),
        "records": store.records() if store else [],
        "reviewed_pages": reviewed_pages,
        "reviewed_documents": reviewed_docs,
        "kv": _score(score["kv"]),
        "heading": {
            **_score(score["heading"]),
            "precision": score["heading"]["precision"],
            "recall": score["heading"]["recall"],
        },
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
    infos = [_run_info(path, dirs) for path in dirs]  # newest first

    # Distinct documents across runs (a rerun of a batch is the same documents), with their page counts.
    pages_of: dict[str, int] = {}
    for path in reversed(dirs):  # oldest first, so the newest run's count wins
        for doc in (_run_meta(path).get("documents") or []):
            if doc.get("record_id"):
                pages_of[doc["record_id"]] = int(doc.get("page_count") or 0)
    documents, pages = len(pages_of), sum(pages_of.values())
    batches = {frozenset(info["records"]) for info in infos if info["records"]}
    timed_pages = sum(info["completed_pages"] for info in infos if info["total_time_seconds"])
    total_time = sum(info["total_time_seconds"] for info in infos)
    latest = next((info for info in infos if info["reviewed_pages"] or info["reviewed_documents"]), None)

    return {
        "runs": infos,
        "active_run": _active_run(infos),
        "totals": {
            "batches": len(batches),
            "runs": len(infos),
            "documents": documents,
            "pages": pages,
            "avg_pages_per_document": round(pages / documents, 1) if documents else None,
            "avg_time_per_page": round(total_time / timed_pages, 3) if timed_pages else None,
            "latest": None
            if latest is None
            else {
                "run": latest["id"],
                "model_version": latest["model_version"],
                "kv_accuracy": latest["kv"]["accuracy"],
                "heading_accuracy": latest["heading"]["accuracy"],
                "documents": latest["reviewed_documents"],
            },
        },
    }


@app.get("/api/meta")
def meta() -> dict[str, Any]:
    return {
        "fields": [{"id": field, "label": label} for field, label in labels.FIELDS.items()],
        "reasons": [{"id": code, "label": label} for code, label in labels.REASONS.items()],
        "heading_reasons": [{"id": code, "label": label} for code, label in labels.HEADING_REASONS.items()],
        "correction_reasons": sorted(labels.CORRECTION_REASONS),
        "key_required_reasons": sorted(labels.KEY_REQUIRED_REASONS),
        "group_reason": "wrong_group",
        "groups": [{"id": field, "label": label} for field, label in labels.KV_FIELDS.items()],
        "levels": list(labels.LEVELS),
    }


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    run_dir = _resolve_run_dir(run_id)
    if not labels.has_workbook(run_dir):
        raise HTTPException(409, f"{run_id} has no {config.Workbook_Name}; rerun it to review.")
    dirs = _run_dirs()
    info = _run_info(run_dir, dirs)
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
    return {**info, **labels.store_status(run_dir), "field_count": len(labels.FIELDS), "documents": out}


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
    return {"record_id": record_id, "page_number": page_number, "file_name": file_name, "fields": fields}


@app.get("/api/runs/{run_id}/page-ocr")
def get_page_ocr(run_id: str, record_id: str, file_name: str) -> dict[str, Any]:
    _resolve_run_dir(run_id)
    return page_lines(record_id, file_name)


@app.get("/api/runs/{run_id}/image")
def get_image(run_id: str, record_id: str, file_name: str) -> FileResponse:
    """The original page image. The key-value extraction never reads it; the review shows it so a
    reviewer can see the page the OCR words came from."""
    _resolve_run_dir(run_id)
    if "/" in record_id or "\\" in record_id or ".." in record_id or "/" in file_name or "\\" in file_name or ".." in file_name:
        raise HTTPException(404, "image not found")
    path = Path(config.Raw_Input) / record_id / file_name
    if path.is_file():
        return FileResponse(path)
    raise HTTPException(404, f"image not found for {record_id}/{file_name}")


class Verdict(BaseModel):
    verdict: str = ""
    reason: str = ""
    belongs_to: str = ""
    level: str = ""


class AddedValue(BaseModel):
    value_words: list[int] = Field(default_factory=list)
    key_words: list[int] = Field(default_factory=list)
    for_candidate: str = ""
    level: str = ""


class ReviewBody(BaseModel):
    record_id: str
    page_number: str
    file_name: str
    field: str
    reviewer: str = ""
    not_present: bool = False
    candidates: dict[str, Verdict] = Field(default_factory=dict)
    added: list[AddedValue] = Field(default_factory=list)


def _reviewed_count(run_dir: Path, record_id: str, page_number: str, file_name: str) -> int:
    key = labels.page_key(record_id, page_number, file_name)
    return labels.reviewed_fields(run_dir, labels.pooled_labels(_run_dirs())).get(key, 0)


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
    return {
        "review": label,
        "reviewed_fields": _reviewed_count(run_dir, body.record_id, body.page_number, body.file_name),
        **labels.store_status(run_dir),
    }


@app.delete("/api/runs/{run_id}/review")
def clear_review(run_id: str, record_id: str, page_number: str, file_name: str, field: str) -> dict[str, Any]:
    run_dir = _resolve_run_dir(run_id)
    labels.clear_field_review(run_dir, record_id, page_number, file_name, field)
    return {"reviewed_fields": _reviewed_count(run_dir, record_id, page_number, file_name), **labels.store_status(run_dir)}


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


# ---------------------------------------------------------------- accuracy by model version


@app.get("/api/runs/{run_id}/versions-accuracy")
def versions_accuracy(run_id: str) -> dict[str, Any]:
    """Accuracy of every run over this run's documents, grouped by extraction model version.

    All runs are scored on the same labels: the latest review of each page from any run.
    """
    run_dir = _resolve_run_dir(run_id)
    store = labels.RunStore.get(run_dir)
    if store is None:
        raise HTTPException(409, f"{run_id} has no {config.Workbook_Name}.")
    records = frozenset(store.records())
    dirs = _run_dirs()  # newest first
    groups: dict[str, list[dict[str, Any]]] = {}
    for path in dirs:
        other = labels.RunStore.get(path)
        if other is None or frozenset(other.records()) != records:
            continue
        info = _run_info(path, dirs)
        score = _evaluation(path, dirs)
        groups.setdefault(info["model_version"], []).append(
            {
                "id": path.name,
                "start_time": info["start_time"],
                "status": info["status"],
                "accuracy": score["accuracy"],
                "correct": score["correct"],
                "wrong": score["wrong"],
                "missed": score["missed"],
                "total": score["total"],
                "fields": score["fields"],
                "kv": score["kv"],
                "heading": score["heading"],
                "headings": score["headings"],
            }
        )
    known = {version["id"]: version for version in registry.list_versions()}
    out = [
        {"model_version": version, "label": known.get(version, {}).get("label", version), "latest": runs[0], "runs": runs}
        for version, runs in sorted(groups.items())
    ]
    return {
        "run_id": run_id,
        "documents": len(records),
        "fields": [{"id": field, "label": label} for field, label in labels.KV_FIELDS.items()],
        "heading_fields": [{"id": field, "label": label} for field, label in labels.HEADING_FIELDS.items()],
        "versions": out,
    }

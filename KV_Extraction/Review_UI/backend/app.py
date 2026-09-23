"""Local API for KV extraction manual review.

Serves KV_Run folders, page images/overlays, and Accuracy saves.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
KV_ROOT = HERE.parent.parent
if str(KV_ROOT) not in sys.path:
    sys.path.insert(0, str(KV_ROOT))

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from Util import config
from Util.geometry import group_lines, words_from_page

app = FastAPI(title="KV Extraction Review")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FIELD_PREFIXES = ("dob", "ID", "MName", "PName", "ESig")
OVERLAY_FIELDS = {
    "dob": "dob",
    "ID": "member_id",
    "MName": "name",
    "PName": "provider_name",
    "ESig": "electronic_signature",
}
# Separate review workbook — one sheet per extractor (not extraction.xlsx).
REVIEW_EXCEL_NAME = "manual_review.xlsx"
REVIEW_SHEETS: dict[str, str] = {
    "dob": "DOB",
    "ID": "Member_ID",
    "MName": "Member_Name",
    "PName": "Provider_Name",
    "ESig": "E_Signature",
}
MISSED_KEYS_SHEET = "Missed_Keys"
EXTRACTOR_LABELS = {
    "dob": "DOB",
    "ID": "Member ID",
    "MName": "Member Name",
    "PName": "Provider Name",
    "ESig": "E Signature",
}


def _run_dirs() -> list[Path]:
    """Runs the Review UI can open.

    If config.Review_Run is set to a KV_Run folder, only that folder is used.
    Otherwise every KV_Run_* under config.Local_Output is listed.
    """
    pinned = getattr(config, "Review_Run", None)
    if pinned is not None:
        path = Path(pinned)
        if path.is_dir():
            return [path]
        return []
    root = Path(config.Local_Output)
    if not root.is_dir():
        return []
    return sorted(
        (path for path in root.iterdir() if path.is_dir() and path.name.startswith("KV_Run_")),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def _resolve_run_dir(run_id: str) -> Path:
    """Map a run id (folder name) to an absolute path.

    Prefers an exact match under the configured run list so a pinned Review_Run
    works even if it lives outside Local_Output.
    """
    for path in _run_dirs():
        if path.name == run_id:
            return path
    fallback = Path(config.Local_Output) / run_id
    if fallback.is_dir():
        return fallback
    raise HTTPException(404, "run not found")


def _reviews_path(run_dir: Path) -> Path:
    return run_dir / "reviews.json"


def _review_excel_path(run_dir: Path) -> Path:
    return run_dir / REVIEW_EXCEL_NAME


def _normalize_review(raw: Any) -> dict[str, str] | None:
    """Accept legacy overall accuracy and the key/value split payload."""
    if isinstance(raw, str):
        value = raw.strip().casefold()
        if value not in {"correct", "incorrect"}:
            return None
        return {
            "key_accuracy": value,
            "value_accuracy": value,
            "reason": "",
            "actual_key": "",
            "actual_value": "",
        }
    if isinstance(raw, dict):
        key_acc = str(raw.get("key_accuracy") or "").strip().casefold()
        value_acc = str(raw.get("value_accuracy") or "").strip().casefold()
        legacy = str(raw.get("accuracy") or "").strip().casefold()
        if legacy in {"correct", "incorrect"} and not key_acc and not value_acc:
            key_acc = legacy
            value_acc = legacy
        if key_acc not in {"correct", "incorrect"} or value_acc not in {"correct", "incorrect"}:
            return None
        if key_acc == "incorrect":
            value_acc = "incorrect"
        return {
            "key_accuracy": key_acc,
            "value_accuracy": value_acc,
            "reason": str(raw.get("reason") or "").strip(),
            "actual_key": str(raw.get("actual_key") or "").strip(),
            "actual_value": str(raw.get("actual_value") or "").strip(),
        }
    return None


def _load_reviews(run_dir: Path) -> dict[str, dict[str, str]]:
    path = _reviews_path(run_dir)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for hit_id, raw in data.items():
        normalized = _normalize_review(raw)
        if normalized:
            out[str(hit_id)] = normalized
    return out


def _save_reviews(run_dir: Path, reviews: dict[str, dict[str, str]]) -> None:
    _reviews_path(run_dir).write_text(json.dumps(reviews, indent=2) + "\n", encoding="utf-8")


def _missed_keys_path(run_dir: Path) -> Path:
    return run_dir / "missed_keys.json"


def _load_missed_keys(run_dir: Path) -> list[dict[str, str]]:
    path = _missed_keys_path(run_dir)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    rows: list[dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        field = str(item.get("field") or "").strip()
        key = str(item.get("key") or "").strip()
        value = str(item.get("value") or "").strip()
        if field not in FIELD_PREFIXES or not key:
            continue
        rows.append(
            {
                "record_id": str(item.get("record_id") or ""),
                "file_name": str(item.get("file_name") or ""),
                "page_number": str(item.get("page_number") or ""),
                "field": field,
                "key": key,
                "value": value,
            }
        )
    return rows


def _save_missed_keys(run_dir: Path, rows: list[dict[str, str]]) -> None:
    _missed_keys_path(run_dir).write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")


def _page_missed_keys(
    missed: list[dict[str, str]],
    record_id: str,
    page_number: str,
    file_name: str,
) -> list[dict[str, str]]:
    return [
        {"field": row["field"], "key": row["key"], "value": row.get("value", "")}
        for row in missed
        if row["record_id"] == record_id
        and row["page_number"] == page_number
        and row["file_name"] == file_name
    ]


def _parse_list_cell(raw: Any) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [str(item) for item in data]
    except json.JSONDecodeError:
        pass
    return [text]


def _split_keyed(item: str) -> tuple[str, str]:
    if ": " in item:
        key, value = item.split(": ", 1)
        return key, value
    return item, ""


def _hit_id(record_id: str, page_number: str, file_name: str, prefix: str, key: str) -> str:
    return f"{record_id}|{page_number}|{file_name}|{prefix}|{key}"


def _load_extraction(run_dir: Path) -> pd.DataFrame:
    path = run_dir / "extraction.xlsx"
    if not path.is_file():
        raise HTTPException(404, f"extraction.xlsx missing in {run_dir.name}")
    return pd.read_excel(path, sheet_name="Extraction", dtype=str).fillna("")


def _page_hits(row: pd.Series, reviews: dict[str, dict[str, str]]) -> list[dict[str, Any]]:
    record_id = str(row.get("RecordId") or "")
    page_number = str(row.get("PageNumber") or "")
    file_name = str(row.get("FileName") or "")
    hits: list[dict[str, Any]] = []
    for prefix in FIELD_PREFIXES:
        keys = _parse_list_cell(row.get(f"{prefix}_Key"))
        if not keys:
            continue
        regions = _parse_list_cell(row.get(f"{prefix}_Region"))
        sentences = _parse_list_cell(row.get(f"{prefix}_Sentence"))
        ner_texts = _parse_list_cell(row.get(f"{prefix}_Ner_Text"))
        scores = _parse_list_cell(row.get(f"{prefix}_Score"))
        accepted = _parse_list_cell(row.get(f"{prefix}_Accepted"))
        selected = _parse_list_cell(row.get(f"{prefix}_Selected"))
        sources = _parse_list_cell(row.get(f"{prefix}_Source"))
        if prefix == "ESig":
            values = _parse_list_cell(row.get(f"{prefix}_ProviderName"))
            dates = _parse_list_cell(row.get(f"{prefix}_SignatureDate"))
        else:
            values = _parse_list_cell(row.get(f"{prefix}_Value"))
            dates = []
        for index, key in enumerate(keys):
            _, region = _split_keyed(regions[index]) if index < len(regions) else (key, "")
            _, sentence = _split_keyed(sentences[index]) if index < len(sentences) else (key, "")
            _, ner = _split_keyed(ner_texts[index]) if index < len(ner_texts) else (key, "")
            _, value = _split_keyed(values[index]) if index < len(values) else (key, "")
            _, score = _split_keyed(scores[index]) if index < len(scores) else (key, "")
            _, acc_flag = _split_keyed(accepted[index]) if index < len(accepted) else (key, "")
            _, sel_flag = _split_keyed(selected[index]) if index < len(selected) else (key, "")
            _, source = _split_keyed(sources[index]) if index < len(sources) else (key, "")
            _, sig_date = _split_keyed(dates[index]) if index < len(dates) else (key, "")
            hid = _hit_id(record_id, page_number, file_name, prefix, key)
            review = reviews.get(hid) or {}
            hits.append(
                {
                    "id": hid,
                    "field": prefix,
                    "key": key,
                    "region": region,
                    "sentence": sentence,
                    "ner_text": ner,
                    "value": value,
                    "signature_date": sig_date,
                    "score": score,
                    "accepted": acc_flag,
                    "selected": sel_flag,
                    "source": source,
                    "accuracy": review.get("key_accuracy", ""),  # legacy alias for UI count
                    "key_accuracy": review.get("key_accuracy", ""),
                    "value_accuracy": review.get("value_accuracy", ""),
                    "reason": review.get("reason", ""),
                    "actual_key": review.get("actual_key", ""),
                    "actual_value": review.get("actual_value", ""),
                }
            )
    return hits


def _iter_extraction_hits(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Flatten extraction.xlsx into one training-friendly row per hit."""
    rows: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        record_id = str(row.get("RecordId") or "")
        page_number = str(row.get("PageNumber") or "")
        file_name = str(row.get("FileName") or "")
        for prefix in FIELD_PREFIXES:
            keys = _parse_list_cell(row.get(f"{prefix}_Key"))
            if not keys:
                continue
            regions = _parse_list_cell(row.get(f"{prefix}_Region"))
            sentences = _parse_list_cell(row.get(f"{prefix}_Sentence"))
            ner_texts = _parse_list_cell(row.get(f"{prefix}_Ner_Text"))
            scores = _parse_list_cell(row.get(f"{prefix}_Score"))
            accepted = _parse_list_cell(row.get(f"{prefix}_Accepted"))
            selected = _parse_list_cell(row.get(f"{prefix}_Selected"))
            sources = _parse_list_cell(row.get(f"{prefix}_Source"))
            if prefix == "ESig":
                values = _parse_list_cell(row.get(f"{prefix}_ProviderName"))
                dates = _parse_list_cell(row.get(f"{prefix}_SignatureDate"))
            else:
                values = _parse_list_cell(row.get(f"{prefix}_Value"))
                dates = []
            for index, key in enumerate(keys):
                _, region = _split_keyed(regions[index]) if index < len(regions) else (key, "")
                _, sentence = _split_keyed(sentences[index]) if index < len(sentences) else (key, "")
                _, ner = _split_keyed(ner_texts[index]) if index < len(ner_texts) else (key, "")
                _, value = _split_keyed(values[index]) if index < len(values) else (key, "")
                _, score = _split_keyed(scores[index]) if index < len(scores) else (key, "")
                _, acc_flag = _split_keyed(accepted[index]) if index < len(accepted) else (key, "")
                _, sel_flag = _split_keyed(selected[index]) if index < len(selected) else (key, "")
                _, source = _split_keyed(sources[index]) if index < len(sources) else (key, "")
                _, sig_date = _split_keyed(dates[index]) if index < len(dates) else (key, "")
                base = {
                    "RecordId": record_id,
                    "FileName": file_name,
                    "PageNumber": page_number,
                    "Key": key,
                    "Region": region,
                    "Sentence": sentence,
                    "Ner_Text": ner,
                    "Score": score,
                    "Accepted": acc_flag,
                    "Selected": sel_flag,
                    "Source": source,
                    "field": prefix,
                    "hit_id": _hit_id(record_id, page_number, file_name, prefix, key),
                }
                if prefix == "ESig":
                    base["ProviderName"] = value
                    base["SignatureDate"] = sig_date
                else:
                    base["Value"] = value
                rows.append(base)
    return rows


def _write_manual_review_excel(
    run_dir: Path,
    reviews: dict[str, dict[str, str]],
    missed_keys: list[dict[str, str]] | None = None,
) -> None:
    """Write `{run}/manual_review.xlsx` with one sheet per extractor + Missed_Keys."""
    frame = _load_extraction(run_dir)
    hits = _iter_extraction_hits(frame)
    sheets: dict[str, list[dict[str, str]]] = {name: [] for name in REVIEW_SHEETS.values()}
    for hit in hits:
        prefix = str(hit["field"])
        sheet = REVIEW_SHEETS[prefix]
        review = reviews.get(str(hit["hit_id"])) or {}
        key_acc = review.get("key_accuracy", "")
        value_acc = review.get("value_accuracy", "")
        any_incorrect = key_acc == "incorrect" or value_acc == "incorrect"
        reason = review.get("reason", "") if any_incorrect else ""
        actual_key = review.get("actual_key", "") if key_acc == "incorrect" else ""
        actual = review.get("actual_value", "") if value_acc == "incorrect" else ""
        if prefix == "ESig":
            row = {
                "RecordId": hit["RecordId"],
                "FileName": hit["FileName"],
                "PageNumber": hit["PageNumber"],
                "Key": hit["Key"],
                "Region": hit["Region"],
                "Sentence": hit["Sentence"],
                "Ner_Text": hit["Ner_Text"],
                "ProviderName": hit.get("ProviderName", ""),
                "SignatureDate": hit.get("SignatureDate", ""),
                "Score": hit["Score"],
                "Accepted": hit["Accepted"],
                "Selected": hit["Selected"],
                "Source": hit["Source"],
                "KeyAccuracy": key_acc,
                "ValueAccuracy": value_acc,
                "ReasonForIncorrect": reason,
                "ActualCorrectKey": actual_key,
                "ActualCorrectValue": actual,
            }
        else:
            row = {
                "RecordId": hit["RecordId"],
                "FileName": hit["FileName"],
                "PageNumber": hit["PageNumber"],
                "Key": hit["Key"],
                "Region": hit["Region"],
                "Sentence": hit["Sentence"],
                "Ner_Text": hit["Ner_Text"],
                "Value": hit.get("Value", ""),
                "Score": hit["Score"],
                "Accepted": hit["Accepted"],
                "Selected": hit["Selected"],
                "Source": hit["Source"],
                "KeyAccuracy": key_acc,
                "ValueAccuracy": value_acc,
                "ReasonForIncorrect": reason,
                "ActualCorrectKey": actual_key,
                "ActualCorrectValue": actual,
            }
        sheets[sheet].append(row)

    if missed_keys is None:
        missed_keys = _load_missed_keys(run_dir)
    missed_rows = [
        {
            "RecordId": row["record_id"],
            "FileName": row["file_name"],
            "PageNumber": row["page_number"],
            "Extractor": EXTRACTOR_LABELS.get(row["field"], row["field"]),
            "Field": row["field"],
            "Key": row["key"],
            "Value": row.get("value", ""),
        }
        for row in missed_keys
    ]

    path = _review_excel_path(run_dir)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet_name in REVIEW_SHEETS.values():
            rows = sheets[sheet_name]
            if sheet_name == "E_Signature":
                columns = [
                    "RecordId",
                    "FileName",
                    "PageNumber",
                    "Key",
                    "Region",
                    "Sentence",
                    "Ner_Text",
                    "ProviderName",
                    "SignatureDate",
                    "Score",
                    "Accepted",
                    "Selected",
                    "Source",
                    "KeyAccuracy",
                    "ValueAccuracy",
                    "ReasonForIncorrect",
                    "ActualCorrectKey",
                    "ActualCorrectValue",
                ]
            else:
                columns = [
                    "RecordId",
                    "FileName",
                    "PageNumber",
                    "Key",
                    "Region",
                    "Sentence",
                    "Ner_Text",
                    "Value",
                    "Score",
                    "Accepted",
                    "Selected",
                    "Source",
                    "KeyAccuracy",
                    "ValueAccuracy",
                    "ReasonForIncorrect",
                    "ActualCorrectKey",
                    "ActualCorrectValue",
                ]
            pd.DataFrame(rows, columns=columns).to_excel(writer, sheet_name=sheet_name, index=False)
        pd.DataFrame(
            missed_rows,
            columns=["RecordId", "FileName", "PageNumber", "Extractor", "Field", "Key", "Value"],
        ).to_excel(writer, sheet_name=MISSED_KEYS_SHEET, index=False)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/runs")
def list_runs() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in _run_dirs():
        excel = path / "extraction.xlsx"
        summary_docs = 0
        summary_pages = 0
        if excel.is_file():
            try:
                summary = pd.read_excel(excel, sheet_name="Extraction Summary", dtype=str).fillna("")
                summary_docs = len(summary)
                summary_pages = int(pd.to_numeric(summary.get("PageCount"), errors="coerce").fillna(0).sum())
            except Exception:  # noqa: BLE001
                pass
        rows.append(
            {
                "id": path.name,
                "path": str(path),
                "has_excel": excel.is_file(),
                "documents": summary_docs,
                "pages": summary_pages,
                "mtime": path.stat().st_mtime,
            }
        )
    return rows


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    run_dir = _resolve_run_dir(run_id)
    frame = _load_extraction(run_dir)
    reviews = _load_reviews(run_dir)
    missed = _load_missed_keys(run_dir)
    documents: dict[str, dict[str, Any]] = {}
    for _, row in frame.iterrows():
        record_id = str(row.get("RecordId") or "")
        if not record_id:
            continue
        doc = documents.setdefault(
            record_id,
            {
                "record_id": record_id,
                "page_count": str(row.get("PageCount") or ""),
                "pages": [],
            },
        )
        page_number = str(row.get("PageNumber") or "")
        file_name = str(row.get("FileName") or "")
        page = {
            "page_number": page_number,
            "file_name": file_name,
            "hits": _page_hits(row, reviews),
            "missed_keys": _page_missed_keys(missed, record_id, page_number, file_name),
        }
        doc["pages"].append(page)
    for doc in documents.values():
        doc["pages"].sort(key=lambda item: int(item["page_number"] or 0))
    return {
        "id": run_id,
        "documents": list(documents.values()),
        "review_count": sum(
            1 for value in reviews.values() if value.get("key_accuracy") and value.get("value_accuracy")
        ),
    }


def _ocr_page(record_id: str, file_name: str) -> dict[str, Any] | None:
    folder = Path(config.OCR_Input) / record_id
    if not folder.is_dir():
        return None
    jsons = sorted(path for path in folder.glob("*.json") if path.is_file())
    if not jsons:
        return None
    path = max(jsons, key=lambda item: item.stat().st_size)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    pages = data.get("pages") if isinstance(data, dict) else None
    if not isinstance(pages, list):
        return None
    target = file_name.casefold()
    for page in pages:
        if not isinstance(page, dict):
            continue
        if str(page.get("fileName") or "").casefold() == target:
            return page
    return None


def _page_ocr_text(page: dict[str, Any]) -> str:
    content = str(page.get("content") or "").strip()
    if content:
        return content
    words = words_from_page(page)
    if not words:
        return ""
    return "\n".join(" ".join(word.content for word in line) for line in group_lines(words))


@app.get("/api/runs/{run_id}/ocr-text", response_class=PlainTextResponse)
def get_ocr_text(run_id: str, record_id: str, file_name: str) -> str:
    run_dir = _resolve_run_dir(run_id)
    page = _ocr_page(record_id, file_name)
    if page is None:
        return "OCR output is not available for this page."
    text = _page_ocr_text(page)
    return text or "OCR output is empty for this page."


@app.get("/api/runs/{run_id}/image")
def get_image(run_id: str, record_id: str, file_name: str, kind: str = "overall") -> FileResponse:
    run_dir = _resolve_run_dir(run_id)
    candidates: list[Path] = []
    if kind == "raw":
        candidates.append(Path(config.Raw_Input) / record_id / file_name)
    elif kind == "overall":
        overall = run_dir / "overlays" / "overall" / record_id
        if overall.is_dir():
            candidates.extend(sorted(overall.glob(f"page_*_{file_name}")))
        candidates.append(Path(config.Raw_Input) / record_id / file_name)
    else:
        field = OVERLAY_FIELDS.get(kind, kind)
        folder = run_dir / "overlays" / field / record_id
        if folder.is_dir():
            candidates.extend(sorted(folder.glob(f"page_*_{file_name}")))
        candidates.append(Path(config.Raw_Input) / record_id / file_name)
    for path in candidates:
        if path.is_file():
            return FileResponse(path)
    raise HTTPException(404, f"image not found for {record_id}/{file_name} kind={kind}")


class AccuracyBody(BaseModel):
    hit_id: str
    key_accuracy: str  # "", "correct", "incorrect"
    value_accuracy: str = ""
    reason: str = ""
    actual_key: str = ""
    actual_value: str = ""


@app.post("/api/runs/{run_id}/accuracy")
def set_accuracy(run_id: str, body: AccuracyBody) -> dict[str, Any]:
    run_dir = _resolve_run_dir(run_id)
    key_acc = body.key_accuracy.strip().casefold()
    value_acc = body.value_accuracy.strip().casefold()
    if key_acc not in {"", "correct", "incorrect"} or value_acc not in {"", "correct", "incorrect"}:
        raise HTTPException(400, "key_accuracy and value_accuracy must be '', correct, or incorrect")
    if bool(key_acc) != bool(value_acc):
        raise HTTPException(400, "key_accuracy and value_accuracy must both be set or both cleared")
    if key_acc == "incorrect":
        value_acc = "incorrect"
    reason = body.reason.strip()
    actual_key = body.actual_key.strip()
    actual_value = body.actual_value.strip()
    if key_acc == "incorrect" or value_acc == "incorrect":
        if not reason:
            raise HTTPException(400, "reason is required when key or value is incorrect")
        if key_acc == "incorrect" and not actual_key:
            raise HTTPException(400, "actual_key is required when key is incorrect")
        if value_acc == "incorrect" and not actual_value:
            raise HTTPException(400, "actual_value is required when value is incorrect")
    reviews = _load_reviews(run_dir)
    if key_acc:
        reviews[body.hit_id] = {
            "key_accuracy": key_acc,
            "value_accuracy": value_acc,
            "reason": reason if key_acc == "incorrect" or value_acc == "incorrect" else "",
            "actual_key": actual_key if key_acc == "incorrect" else "",
            "actual_value": actual_value if value_acc == "incorrect" else "",
        }
    else:
        reviews.pop(body.hit_id, None)
    _save_reviews(run_dir, reviews)
    _write_manual_review_excel(run_dir, reviews)
    return {
        "ok": True,
        "hit_id": body.hit_id,
        "key_accuracy": key_acc,
        "value_accuracy": value_acc,
        "reason": reason if key_acc == "incorrect" or value_acc == "incorrect" else "",
        "actual_key": actual_key if key_acc == "incorrect" else "",
        "actual_value": actual_value if value_acc == "incorrect" else "",
        "review_excel": str(_review_excel_path(run_dir)),
    }


class MissedKeyItem(BaseModel):
    field: str
    key: str
    value: str = ""


class MissedKeysBody(BaseModel):
    record_id: str
    file_name: str
    page_number: str
    keys: list[MissedKeyItem]


@app.post("/api/runs/{run_id}/missed-keys")
def set_missed_keys(run_id: str, body: MissedKeysBody) -> dict[str, Any]:
    run_dir = _resolve_run_dir(run_id)
    cleaned: list[dict[str, str]] = []
    for item in body.keys:
        field = item.field.strip()
        key = item.key.strip()
        value = item.value.strip()
        if not key or not value:
            continue
        if field not in FIELD_PREFIXES:
            raise HTTPException(400, f"unknown extractor field: {field}")
        cleaned.append(
            {
                "record_id": body.record_id,
                "file_name": body.file_name,
                "page_number": body.page_number,
                "field": field,
                "key": key,
                "value": value,
            }
        )
    existing = _load_missed_keys(run_dir)
    kept = [
        row
        for row in existing
        if not (
            row["record_id"] == body.record_id
            and row["file_name"] == body.file_name
            and row["page_number"] == body.page_number
        )
    ]
    merged = kept + cleaned
    _save_missed_keys(run_dir, merged)
    reviews = _load_reviews(run_dir)
    _write_manual_review_excel(run_dir, reviews, merged)
    return {
        "ok": True,
        "count": len(cleaned),
        "keys": cleaned,
        "review_excel": str(_review_excel_path(run_dir)),
    }

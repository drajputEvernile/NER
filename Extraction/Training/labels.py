"""Manual review labels: what reviewers say is on each page, per field.

Everything lives in the run's one workbook ({run}/KV_Extraction.xlsx, see Training/features.py):
a review is written onto the field sheet's own rows, so extraction and review travel together.

    candidate rows   the reviewer's verdict on each key-value pair the run extracted:
                     accuracy right / wrong, review_reason (why it is wrong), belongs_to (the
                     group a key belongs to instead), review_level (a heading's level)
    review rows      source "review": a true value the run did not extract (accuracy "missed"), or
                     the value picked for a wrong candidate (accuracy "fixed", for_candidate). The
                     reviewer selects the key and value words on the OCR; nothing is typed, and the
                     row keeps the sentence around them, built like a candidate's
    reviewed_at      stamped on every row of a reviewed page-field. A page-field with nothing right
                     and nothing picked is "not on the page"

load_labels rebuilds one entry per page and field from those rows:

    candidates  verdict (correct / incorrect + reason) on each extracted candidate
    added       values the rules missed or got wrong, with the OCR words the reviewer picked
    not_present the field is not on the page
    truth       every true value (correct candidates + added), which is what accuracy, ranker
                training and NER training read

Accuracy counts key-value pairs (score_pairs). A pair the run extracted is right when a true
pair has the same key and value (same candidate, or the same value words under the same key
words); an extracted pair with a wrong key or value is wrong; a true pair the run did not
extract is missed. Accuracy = right / (right + wrong + missed). A field not on the page with
nothing extracted is one right; the value picked for a wrong candidate is the same error as that
candidate, not a second miss. Labels are tied to the OCR text by ocr_sha1, so a rerun over the
same pages is scored with the same labels.

Heading fields (one per layout detector, see Heading/extract.py) are reviewed the same way with
their own reasons (noise / not a heading); a correct heading and an added one carry a level
(Heading / Subheading). Headings are scored separately (evaluate_headings: line-level precision /
recall, level accuracy and pages exactly right) and are not part of the KV accuracy.

The workbook is held in memory per run (RunStore) and written back a few seconds after the last
change, atomically. If Excel has it open the write waits and retries (RunStore.error says so), and
nothing is lost.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from Heading.extract import DETECTORS, LEVELS
from Util import config
from Util.geometry import Box, Word, union_boxes
from Util.workbook import read_workbook, write_workbook

from .features import (
    COLUMNS,
    HEADING_SHEET,
    OVERALL_COLUMNS,
    OVERALL_SHEET,
    REVIEW_COLUMNS,
    REVIEW_SOURCE,
    SHEETS,
    run_accepted as _run_accepted,
    run_level,
    run_selected as _run_selected,
    sentence_json,
    sheet_columns,
    sheet_to_log,
)
from .normalize import normalize_selected
from .ocr import page_words

KV_FIELDS: dict[str, str] = {
    "dob": "Member DOB",
    "member_id": "Member ID",
    "name": "Member Name",
    "provider_name": "Provider Name",
    "electronic_signature": "E-Signature",
    "dos": "Date of Service",
    "page_no": "Page No",
}
HEADING_FIELDS: dict[str, str] = {name: detector.label for name, detector in DETECTORS.items()}
FIELDS: dict[str, str] = {**KV_FIELDS, **HEADING_FIELDS}

REASONS: dict[str, str] = {
    "wrong_value": "Wrong Value",
    "wrong_key_value": "Wrong Key & Value",
    "wrong_group": "Key Belongs to Another Group",
    "wrong_position": "Wrong Position",
}

HEADING_REASONS: dict[str, str] = {
    "noise": "Noise",
    "not_heading": "Not a Heading/Sub Heading",
}

# Reasons whose candidate can get the right words selected on the OCR: the value (Wrong Value, the
# key is kept), or the key and the value (Wrong Key & Value, Wrong Position). Selecting is optional:
# a wrong candidate may have no right counterpart (a stray number taken as a page number), or the
# right value may already be another candidate on the page.
CORRECTION_REASONS = frozenset({"wrong_value", "wrong_key_value", "wrong_position"})
# Of those, the ones that also offer the key to select.
KEY_REQUIRED_REASONS = frozenset({"wrong_key_value", "wrong_position"})


def page_key(record_id: str, page_number: str | int, file_name: str) -> str:
    return f"{record_id}|{page_number}|{file_name}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ints(text: Any) -> list[int]:
    return [int(part) for part in str(text or "").split() if part.lstrip("-").isdigit()]


def _joined(indexes: list[int]) -> str:
    return " ".join(map(str, indexes))


def _truthy(value: Any) -> bool:
    return str(value) in {"1", "True", "true"}


# ---------------------------------------------------------------- the run's workbook, in memory


def workbook_path(run_dir: Path) -> Path:
    return Path(run_dir) / config.Workbook_Name


def has_workbook(run_dir: Path) -> bool:
    return workbook_path(run_dir).is_file()


def sheet_name(field: str) -> str:
    return HEADING_SHEET if field in HEADING_FIELDS else SHEETS[field]


_SHEET_COLUMNS = {**{name: sheet_columns(False) for name in SHEETS.values()}, HEADING_SHEET: sheet_columns(True), OVERALL_SHEET: OVERALL_COLUMNS}


class RunStore:
    """One run's workbook held in memory: sheets as frames of strings, written back shortly after a change."""

    FLUSH_DELAY = 3.0
    RETRY_DELAY = 5.0
    _stores: dict[str, "RunStore"] = {}
    _guard = threading.Lock()

    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir)
        self.path = workbook_path(run_dir)
        self.lock = threading.RLock()
        self.sheets: dict[str, pd.DataFrame] = {}
        self.loaded_mtime = 0.0
        self.version = 0
        self.dirty = False
        self.error = ""
        self._timer: threading.Timer | None = None
        self._labels: tuple[int, dict[str, Any]] | None = None
        self._log: tuple[int, pd.DataFrame] | None = None

    @classmethod
    def get(cls, run_dir: Path) -> "RunStore | None":
        """The run's store, or None when the run has no workbook (a run from an older layout)."""
        key = str(Path(run_dir).resolve())
        with cls._guard:
            store = cls._stores.get(key)
            if store is None:
                store = cls._stores[key] = RunStore(run_dir)
        store._refresh()
        return store if store.sheets else None

    @classmethod
    def flush_all(cls) -> None:
        for store in list(cls._stores.values()):
            store.flush()

    def _refresh(self) -> None:
        with self.lock:
            if self.dirty:
                return
            try:
                mtime = self.path.stat().st_mtime
            except OSError:
                self.sheets = {}
                return
            if self.sheets and mtime == self.loaded_mtime:
                return
            sheets = read_workbook(self.path)
            for name, columns in _SHEET_COLUMNS.items():
                frame = sheets.get(name)
                if frame is None:
                    frame = pd.DataFrame(columns=columns)
                for column in columns:
                    if column not in frame.columns:
                        frame[column] = ""
                sheets[name] = frame
            self.sheets = sheets
            self.loaded_mtime = mtime
            self.version += 1

    # ---- what the rest of the code reads

    def log(self) -> pd.DataFrame:
        """Every candidate of the run in the candidate-log columns (the reviewer's own rows left out)."""
        with self.lock:
            if self._log is None or self._log[0] != self.version:
                frames = [sheet_to_log(self.sheets[SHEETS[field]], field) for field in KV_FIELDS]
                heading = self.sheets[HEADING_SHEET]
                for field in HEADING_FIELDS:
                    frames.append(sheet_to_log(heading[heading["field"] == field], field))
                frames = [frame for frame in frames if not frame.empty]
                self._log = (self.version, pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUMNS))
            return self._log[1]

    def records(self) -> list[str]:
        """Record ids in the run, in workbook order."""
        with self.lock:
            return list(dict.fromkeys(self.sheets[OVERALL_SHEET]["RecordId"]))

    def labels(self) -> dict[str, Any]:
        with self.lock:
            if self._labels is None or self._labels[0] != self.version:
                self._labels = (self.version, _labels_from_sheets(self.sheets))
            return self._labels[1]

    # ---- changes

    def changed(self) -> None:
        with self.lock:
            self.version += 1
            self.dirty = True
            self._schedule(self.FLUSH_DELAY)

    def _schedule(self, delay: float) -> None:
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(delay, self.flush)
        self._timer.daemon = True
        self._timer.start()

    def flush(self) -> None:
        with self.lock:
            if not self.dirty:
                return
            snapshot = {name: frame.copy() for name, frame in self.sheets.items()}
            version = self.version
        try:
            write_workbook(self.path, snapshot)
            mtime = self.path.stat().st_mtime
        except OSError as exc:
            with self.lock:
                self.error = f"{self.path.name} could not be saved ({exc}). Close it in Excel; the reviews are kept and saved when it is free."
                self._schedule(self.RETRY_DELAY)
            return
        with self.lock:
            self.loaded_mtime = mtime
            self.error = ""
            if self.version == version:
                self.dirty = False
            else:
                self._schedule(self.FLUSH_DELAY)


atexit.register(RunStore.flush_all)


def store_status(run_dir: Path) -> dict[str, Any]:
    store = RunStore.get(run_dir)
    return {"saving": bool(store and store.dirty), "save_error": store.error if store else ""}


# ---------------------------------------------------------------- candidate logs


def candidate_logs(run_dir: Path) -> list[Path]:
    """The files holding the run's candidates: the workbook (a run from an older layout has none)."""
    return [workbook_path(run_dir)] if has_workbook(run_dir) else []


def run_log(run_dir: Path) -> pd.DataFrame:
    store = RunStore.get(run_dir)
    return store.log() if store else pd.DataFrame(columns=COLUMNS)


def record_log(run_dir: Path, record_id: str) -> pd.DataFrame:
    frame = run_log(run_dir)
    return frame[frame["record_id"] == record_id]


def _box(row: dict[str, Any], prefix: str) -> list[float] | None:
    try:
        return [float(row[f"{prefix}_{name}"]) for name in ("x0", "y0", "x1", "y1")]
    except (KeyError, TypeError, ValueError):
        return None


def page_candidates(run_dir: Path, record_id: str, page_number: str, file_name: str) -> dict[str, dict[str, Any]]:
    """field -> {ocr_sha1, candidates: [...]} for one page, from the run's workbook."""
    frame = record_log(run_dir, record_id)
    page = frame[(frame["page_number"] == str(page_number)) & (frame["file_name"] == file_name)]
    out: dict[str, dict[str, Any]] = {field: {"ocr_sha1": "", "candidates": []} for field in FIELDS}
    for row in page.to_dict("records"):
        field = row["field"]
        if field not in out:
            continue
        out[field]["ocr_sha1"] = row["ocr_sha1"]
        if str(row["is_placeholder"]) == "1":
            continue
        out[field]["candidates"].append(
            {
                "candidate_id": row["candidate_id"],
                "key": row["key"],
                "key_text": row["key_text"],
                "region": row["region"],
                "source": row["rule_source"],
                "score": row["rule_score"],
                "accepted": _run_accepted(row),
                "selected": _run_selected(row),
                "note": row["rule_note"],
                "value": row["value"],
                "value_norm": row["value_norm"],
                "detail": run_level(row),
                "key_words": _ints(row["key_words"]),
                "value_words": _ints(row["value_words"]),
                "key_box": _box(row, "key"),
                "value_box": _box(row, "value"),
            }
        )
    # A field the run did not log (headings in a run made without them) still belongs to this OCR.
    page_hash = next((slot["ocr_sha1"] for slot in out.values() if slot["ocr_sha1"]), "")
    for slot in out.values():
        slot["ocr_sha1"] = slot["ocr_sha1"] or page_hash
    return out


# ---------------------------------------------------------------- scoring


def is_extracted(c: dict[str, Any], heading: bool = False) -> bool:
    """What a review must judge: every key-value pair the run extracted (the pairs accuracy
    counts), or the lines detected as headings."""
    return bool(c["selected"] or (not heading and c["accepted"] and c["value_norm"]))


def _extracted(candidates: list[dict[str, Any]], heading: bool = False) -> list[dict[str, Any]]:
    return [
        {
            "candidate_id": c["candidate_id"], "value": c["value"], "value_norm": c["value_norm"],
            "key_words": c["key_words"], "value_words": c["value_words"],
        }
        for c in candidates
        if is_extracted(c, heading)
    ]


def _norms(items: list[dict[str, Any]]) -> set[str]:
    return {item["value_norm"] for item in items if item.get("value_norm")}


def same_pair(true: dict[str, Any], found: dict[str, Any]) -> bool:
    """Whether an extracted candidate is this true key-value pair: the same candidate, or the
    same words under the same key words. Words are compared by what the reviewer selected on the
    OCR, so each side must cover at least half of the other (a signature picked with its "on" /
    "at" still matches the candidate that read the name and the date); without words on either
    side the normalized values are compared."""
    if true.get("candidate_id") and true["candidate_id"] == found.get("candidate_id"):
        return True
    true_words, found_words = set(true.get("value_words") or []), set(found.get("value_words") or [])
    if true_words and found_words:
        shared = len(true_words & found_words)
        if not shared or shared < 0.5 * len(true_words) or shared < 0.5 * len(found_words):
            return False
    elif not true.get("value_norm") or true["value_norm"] != found.get("value_norm"):
        return False
    true_key = set(true.get("key_words") or [])
    return not true_key or bool(true_key & set(found.get("key_words") or []))


def score_pairs(
    found: list[dict[str, Any]], label: dict[str, Any], details: list[tuple[str, dict[str, Any]]] | None = None
) -> Counter:
    """right / wrong / missed key-value pairs of one reviewed page-field.

    found: the pairs the run extracted (candidate_id, value_norm, key_words, value_words).
    details, when given, gets ("right" | "wrong" | "missed", pair) for every pair counted.
    """
    details = details if details is not None else []
    truth = [item for item in label.get("truth") or [] if item.get("value_norm")]
    verdicts = label.get("candidates") or {}
    score: Counter = Counter()
    if not truth and not found:
        score["right"] += int(bool(label.get("not_present")))
        return score
    open_truth = list(range(len(truth)))
    wrong_ids: set[str] = set()
    for c in found:
        verdict = (verdicts.get(c.get("candidate_id", "")) or {}).get("verdict")
        match = None
        if verdict != "incorrect":
            match = next((i for i in open_truth if same_pair(truth[i], c)), None)
        if match is None:
            score["wrong"] += 1
            wrong_ids.add(c.get("candidate_id", ""))
            details.append(("wrong", c))
        else:
            score["right"] += 1
            open_truth.remove(match)
            details.append(("right", c))
    for i in open_truth:
        if truth[i].get("for_candidate") not in wrong_ids - {""}:
            score["missed"] += 1
            details.append(("missed", truth[i]))
    return score


def auto_review(candidates: list[dict[str, Any]], label: dict[str, Any], heading: bool = False) -> dict[str, Any]:
    """Another run's review of this page-field applied to this run's candidates, judged like
    the accuracy: a candidate matching a true pair is correct, any other extracted one is
    incorrect (its old verdict kept when it has one), and true pairs nothing matched are added."""
    if heading:
        return label
    details: list[tuple[str, dict[str, Any]]] = []
    score_pairs(_extracted(candidates), label, details)
    old = label.get("candidates") or {}
    verdicts: dict[str, dict[str, Any]] = {}
    added: list[dict[str, Any]] = []
    for kind, pair in details:
        cid = pair.get("candidate_id", "")
        if kind == "right":
            verdicts[cid] = {"verdict": "correct", "reason": ""}
        elif kind == "wrong":
            verdicts[cid] = old.get(cid) or {"verdict": "incorrect", "reason": "wrong_key_value"}
        else:
            added.append({**pair, "for_candidate": ""})
    corrected = {cid for cid, verdict in verdicts.items() if verdict.get("reason") in CORRECTION_REASONS}
    added += [item for item in label.get("truth") or [] if item.get("for_candidate") in corrected]
    keep = ("value", "key", "for_candidate", "level", "value_words", "key_words")
    return {
        **label,
        "candidates": verdicts,
        "added": [{name: item.get(name, [] if name.endswith("_words") else "") for name in keep} for item in added],
        "auto": True,
    }


def pair_accuracy(score: Counter) -> float | None:
    total = score["right"] + score["wrong"] + score["missed"]
    return round(100 * score["right"] / total, 1) if total else None


# ---------------------------------------------------------------- the label of a page-field, from its rows


def _label_from_rows(rows: pd.DataFrame, heading: bool) -> dict[str, Any] | None:
    """One page-field's label rebuilt from its workbook rows, or None when it was never reviewed."""
    stamped = rows[rows["reviewed_at"] != ""]
    if stamped.empty:
        return None
    is_review = rows["source"] == REVIEW_SOURCE
    verdicts: dict[str, dict[str, Any]] = {}
    truth: list[dict[str, Any]] = []
    added: list[dict[str, Any]] = []
    extracted: list[dict[str, Any]] = []
    for row in rows[~is_review].to_dict("records"):
        if str(row["is_placeholder"]) == "1":
            continue
        key = "" if row["key"] == "keyless" else row["key"]
        candidate = {
            "candidate_id": row["candidate_id"], "value": row["value"], "value_norm": row["value_norm"],
            "key_words": _ints(row["key_words"]), "value_words": _ints(row["value_words"]),
            "selected": _truthy(row["selected"]), "accepted": _truthy(row["accepted"]),
        }
        if is_extracted(candidate, heading):
            extracted.append({name: candidate[name] for name in ("candidate_id", "value", "value_norm", "key_words", "value_words")})
        if row["accuracy"] not in {"right", "wrong"}:
            continue
        verdicts[row["candidate_id"]] = {
            "verdict": "correct" if row["accuracy"] == "right" else "incorrect",
            "reason": row["review_reason"],
            "belongs_to": row["belongs_to"],
            "level": row["review_level"],
            "key": key,
            "key_text": row["key_text"],
            "region": row["region"],
            "value": row["value"],
            "value_norm": row["value_norm"],
            "detail": row["detail"],
            "selected": candidate["selected"],
        }
        if row["accuracy"] == "right":
            truth.append(
                {
                    "source": "candidate", "candidate_id": row["candidate_id"], "value": row["value"],
                    "value_norm": row["value_norm"], "level": row["review_level"], "key": key,
                    "value_words": candidate["value_words"], "key_words": candidate["key_words"],
                }
            )
    for row in rows[is_review].to_dict("records"):
        item = {
            "candidate_id": row["candidate_id"],
            "value": row["value"],
            "value_norm": row["value_norm"],
            "for_candidate": row["for_candidate"],
            "level": row["review_level"],
            "key": "" if row["key"] == "keyless" else row["key"],
            "value_words": _ints(row["value_words"]),
            "key_words": _ints(row["key_words"]),
        }
        added.append(item)
        truth.append({"source": "added", **item})
    label = {"not_present": not truth, "candidates": verdicts, "truth": truth}
    pairs = score_pairs(extracted, label)
    return {
        **label,
        "added": added,
        "extracted": extracted,
        "pairs": {name: pairs[name] for name in ("right", "wrong", "missed")},
        "correct": _norms(extracted) == _norms(truth) if heading else not (pairs["wrong"] or pairs["missed"]),
        "reviewer": stamped["reviewer"].iloc[0],
        "reviewed_at": max(stamped["reviewed_at"]),
    }


def _labels_from_sheets(sheets: dict[str, pd.DataFrame]) -> dict[str, Any]:
    pages: dict[str, dict[str, Any]] = {}
    for field in FIELDS:
        heading = field in HEADING_FIELDS
        sheet = sheets[sheet_name(field)]
        if heading:
            sheet = sheet[sheet["field"] == field]
        reviewed = sheet[sheet["reviewed_at"] != ""]
        if reviewed.empty:
            continue
        keys = reviewed[["record_id", "page_number", "file_name"]].drop_duplicates().itertuples(index=False, name=None)
        for record_id, page_number, file_name in keys:
            rows = sheet[(sheet["record_id"] == record_id) & (sheet["page_number"] == page_number) & (sheet["file_name"] == file_name)]
            label = _label_from_rows(rows, heading)
            if label is None:
                continue
            key = page_key(record_id, page_number, file_name)
            entry = pages.setdefault(
                key, {"record_id": record_id, "page_number": page_number, "file_name": file_name, "fields": {}, "ocr_sha1": ""}
            )
            entry["ocr_sha1"] = entry["ocr_sha1"] or next((value for value in rows["ocr_sha1"] if value), "")
            entry["fields"][field] = label
    return {"pages": pages}


def load_labels(run_dir: Path) -> dict[str, Any]:
    store = RunStore.get(run_dir)
    return store.labels() if store else {"pages": {}}


# ---------------------------------------------------------------- saving a review


def _text_of(indexes: list[int], by_index: dict[int, Word]) -> str:
    return " ".join(by_index[i].content for i in sorted(indexes) if i in by_index)


def _picked(raw: Any, by_index: dict[int, Word], what: str) -> list[int]:
    indexes = sorted({int(i) for i in raw or []})
    unknown = [i for i in indexes if i not in by_index]
    if unknown:
        raise ValueError(f"The {what} words {unknown} are not on this page's OCR.")
    return indexes


def build_label(
    field: str, found: dict[str, Any], submission: dict[str, Any], reviewer: str, by_index: dict[int, Word]
) -> dict[str, Any]:
    """Validate one page-field review against the run's candidates and derive the truth.

    submission: {not_present, candidates: {candidate_id: {verdict, reason, belongs_to, level}},
    added: [{key_words, value_words, for_candidate, level}]}. The reviewer selects words on the OCR,
    so value and key text come from by_index (OCR word index -> word), never from typed text.
    """
    if field not in FIELDS:
        raise ValueError(f"Unknown field: {field}")
    heading = field in HEADING_FIELDS
    reasons = HEADING_REASONS if heading else REASONS
    candidates = {c["candidate_id"]: c for c in found["candidates"]}

    verdicts: dict[str, dict[str, Any]] = {}
    for cid, raw in (submission.get("candidates") or {}).items():
        if cid not in candidates:
            raise ValueError(f"Candidate {cid} is not on this page for {FIELDS[field]}.")
        verdict = str((raw or {}).get("verdict") or "")
        if verdict not in {"correct", "incorrect"}:
            continue
        reason = str((raw or {}).get("reason") or "")
        if verdict == "incorrect" and reason and reason not in reasons:
            raise ValueError(f"Unknown reason: {reason}")
        c = candidates[cid]
        belongs_to = str(raw.get("belongs_to") or "") if verdict == "incorrect" and reason == "wrong_group" else ""
        if reason == "wrong_group" and verdict == "incorrect" and (belongs_to not in KV_FIELDS or belongs_to == field):
            raise ValueError(f"Pick the group the key of '{c['value']}' belongs to.")
        level = ""
        if heading and verdict == "correct":
            level = str(raw.get("level") or c["detail"])
            if level not in LEVELS:
                raise ValueError(f"Pick the level of the heading '{c['value']}'.")
        verdicts[cid] = {
            "verdict": verdict,
            "reason": reason if verdict == "incorrect" else "",
            "belongs_to": belongs_to,
            "level": level,
            "key": c["key"],
            "key_text": c["key_text"],
            "region": c["region"],
            "value": c["value"],
            "value_norm": c["value_norm"],
            "detail": c["detail"],
            "selected": c["selected"],
        }

    extracted_ids = {c["candidate_id"] for c in candidates.values() if is_extracted(c, heading)}
    if extracted_ids - set(verdicts):
        raise ValueError(f"Mark every extracted {FIELDS[field]} value correct or incorrect first.")
    unreasoned = [
        v for cid, v in verdicts.items() if v["verdict"] == "incorrect" and cid in extracted_ids and not v["reason"]
    ]
    if unreasoned:
        raise ValueError("Pick a reason for each extracted value marked incorrect.")

    added: list[dict[str, Any]] = []
    for raw in submission.get("added") or []:
        value_words = _picked((raw or {}).get("value_words"), by_index, "value")
        key_words = [] if heading else _picked((raw or {}).get("key_words"), by_index, "key")
        if not value_words:
            continue
        for_candidate = str(raw.get("for_candidate") or "")
        if for_candidate:
            reason = verdicts.get(for_candidate, {}).get("reason")
            if reason not in CORRECTION_REASONS:
                raise ValueError("A correction belongs to a candidate marked Wrong Value, Wrong Key & Value or Wrong Position.")
            if not key_words and reason not in KEY_REQUIRED_REASONS:
                # Wrong Value: the key was right, only the value was wrong, so the correction keeps that key.
                key_words = list(candidates[for_candidate]["key_words"])
        level = str(raw.get("level") or "") if heading else ""
        if heading and level not in LEVELS:
            raise ValueError(f"Pick the level of the heading '{_text_of(value_words, by_index)}'.")
        value = _text_of(value_words, by_index)
        added.append(
            {
                "candidate_id": "rev-" + hashlib.sha1(f"{for_candidate}|{key_words}|{value_words}".encode()).hexdigest()[:12],
                "value": value,
                "value_norm": normalize_selected(field, value),
                "for_candidate": for_candidate,
                "level": level,
                "key": _text_of(key_words, by_index),
                "value_words": value_words,
                "key_words": key_words,
            }
        )

    not_present = bool(submission.get("not_present"))
    truth: list[dict[str, Any]] = []
    for cid, v in verdicts.items():
        if v["verdict"] == "correct":
            c = candidates[cid]
            truth.append(
                {
                    "source": "candidate", "candidate_id": cid, "value": c["value"], "value_norm": c["value_norm"],
                    "level": v["level"], "key": c["key"], "value_words": c["value_words"], "key_words": c["key_words"],
                }
            )
    truth.extend({"source": "added", **item} for item in added)

    if not_present and truth:
        if heading:
            raise ValueError("The page is marked as having no headings, but a heading is ticked or added.")
        raise ValueError(f"{FIELDS[field]} is marked not on the page but has correct or added values.")
    if not not_present and not truth:
        if heading:
            raise ValueError("Tick a heading, add a missed one, or mark that the page has no headings.")
        raise ValueError(f"Mark a correct value, add the missed value, or mark {FIELDS[field]} as not on this page.")

    extracted = _extracted(found["candidates"], heading)
    label = {"not_present": not_present, "candidates": verdicts, "truth": truth}
    pairs = score_pairs(extracted, label)
    return {
        **label,
        "added": added,
        "extracted": extracted,
        "pairs": {name: pairs[name] for name in ("right", "wrong", "missed")},
        "correct": _norms(extracted) == _norms(truth) if heading else not (pairs["wrong"] or pairs["missed"]),
        "reviewer": reviewer.strip(),
        "reviewed_at": _now(),
    }


def _review_rows(
    field: str,
    label: dict[str, Any],
    sheet: pd.DataFrame,
    page_rows: pd.DataFrame,
    by_index: dict[int, Word],
    page_w: float,
    page_h: float,
) -> pd.DataFrame:
    """The reviewer's own rows (source "review") for a page-field: each true value the run did not
    extract, with the sentence around the words the reviewer selected."""
    if not label["added"]:
        return pd.DataFrame(columns=sheet.columns)
    words = [by_index[i] for i in sorted(by_index)]
    positions = {word.index: position for position, word in enumerate(words)}
    base = page_rows.iloc[0] if len(page_rows) else None
    rows: list[dict[str, Any]] = []
    for item in label["added"]:
        key_box: Box | None = union_boxes([by_index[i].box for i in item["key_words"]])
        value_box: Box | None = union_boxes([by_index[i].box for i in item["value_words"]])
        row = {column: "" for column in sheet.columns}
        if base is not None:
            for column in ("run_id", "model_version", "record_id", "file_name", "page_number", "page_count", "ocr_sha1", "catalog_hash", "ner_model"):
                row[column] = base[column]
        row.update(
            {
                "candidate_id": item["candidate_id"],
                "is_placeholder": 0,
                "key": item["key"] or "keyless",
                "key_text": item["key"],
                "value": item["value"],
                "value_norm": item["value_norm"],
                "source": REVIEW_SOURCE,
                "sentence": sentence_json(words, positions, item["key_words"], item["value_words"], page_w, page_h),
                "accuracy": "fixed" if item["for_candidate"] else "missed",
                "review_reason": label["candidates"].get(item["for_candidate"], {}).get("reason", ""),
                "review_level": item["level"],
                "for_candidate": item["for_candidate"],
                "key_words": _joined(item["key_words"]),
                "value_words": _joined(item["value_words"]),
                "value_found": 1,
                "page_w": page_w,
                "page_h": page_h,
            }
        )
        if "field" in row:
            row["field"] = field
        for prefix, box in (("key", key_box), ("value", value_box)):
            if box is not None:
                for name, value in zip(("x0", "y0", "x1", "y1"), (box.left / page_w, box.top / page_h, box.right / page_w, box.bottom / page_h)):
                    row[f"{prefix}_{name}"] = round(value, 4)
        rows.append(row)
    return pd.DataFrame(rows, columns=sheet.columns)


def _page_mask(sheet: pd.DataFrame, field: str, record_id: str, page_number: str, file_name: str) -> pd.Series:
    mask = (sheet["record_id"] == record_id) & (sheet["page_number"] == str(page_number)) & (sheet["file_name"] == file_name)
    if field in HEADING_FIELDS:
        mask &= sheet["field"] == field
    return mask


def save_field_review(
    run_dir: Path,
    record_id: str,
    page_number: str,
    file_name: str,
    field: str,
    submission: dict[str, Any],
    reviewer: str,
) -> dict[str, Any]:
    store = RunStore.get(run_dir)
    if store is None:
        raise ValueError(f"{Path(run_dir).name} has no {config.Workbook_Name}; rerun it to review.")
    found = page_candidates(run_dir, record_id, page_number, file_name)
    if field not in found:
        raise ValueError(f"Unknown field: {field}")
    words, page_w, page_h = page_words(record_id, file_name)
    if not words:
        raise ValueError(f"No OCR for {record_id}/{file_name}: the words to select come from it.")
    by_index = {word.index: word for word in words}
    label = build_label(field, found[field], submission, reviewer, by_index)

    with store.lock:
        name = sheet_name(field)
        sheet = store.sheets[name]
        mask = _page_mask(sheet, field, record_id, page_number, file_name)
        if not mask.any():
            raise ValueError(f"The run logged no {FIELDS[field]} rows for {record_id} page {page_number}.")
        # A review replaces the page-field's earlier one: drop its review rows, reset its verdicts.
        sheet = sheet[~(mask & (sheet["source"] == REVIEW_SOURCE))].reset_index(drop=True)
        mask = _page_mask(sheet, field, record_id, page_number, file_name)
        for column in REVIEW_COLUMNS:
            sheet.loc[mask, column] = ""
        for cid, verdict in label["candidates"].items():
            rows = mask & (sheet["candidate_id"] == cid)
            sheet.loc[rows, "accuracy"] = "right" if verdict["verdict"] == "correct" else "wrong"
            sheet.loc[rows, "review_reason"] = verdict["reason"]
            sheet.loc[rows, "belongs_to"] = verdict["belongs_to"]
            sheet.loc[rows, "review_level"] = verdict["level"]
        sheet.loc[mask, "reviewer"] = label["reviewer"]
        sheet.loc[mask, "reviewed_at"] = label["reviewed_at"]
        added = _review_rows(field, label, sheet, sheet[mask], by_index, page_w, page_h)
        if not added.empty:
            added["reviewer"] = label["reviewer"]
            added["reviewed_at"] = label["reviewed_at"]
            sheet = pd.concat([sheet, added], ignore_index=True)
        store.sheets[name] = sheet
        store.changed()
    return label


def clear_field_review(run_dir: Path, record_id: str, page_number: str, file_name: str, field: str) -> None:
    store = RunStore.get(run_dir)
    if store is None:
        return
    with store.lock:
        name = sheet_name(field)
        sheet = store.sheets[name]
        mask = _page_mask(sheet, field, record_id, page_number, file_name)
        if not (mask & (sheet["reviewed_at"] != "")).any():
            return
        sheet = sheet[~(mask & (sheet["source"] == REVIEW_SOURCE))].reset_index(drop=True)
        mask = _page_mask(sheet, field, record_id, page_number, file_name)
        for column in REVIEW_COLUMNS:
            sheet.loc[mask, column] = ""
        store.sheets[name] = sheet
        store.changed()


# ---------------------------------------------------------------- pooled labels and accuracy


def pooled_labels(run_dirs: list[Path]) -> dict[tuple[str, str], dict[str, Any]]:
    """(page key, field) -> latest label across runs, with the page's ocr_sha1."""
    pooled: dict[tuple[str, str], dict[str, Any]] = {}
    for run_dir in run_dirs:
        store = RunStore.get(run_dir)
        if store is None:
            continue
        for key, entry in store.labels()["pages"].items():
            for field, label in entry["fields"].items():
                current = pooled.get((key, field))
                if current is None or label.get("reviewed_at", "") > current.get("reviewed_at", ""):
                    pooled[(key, field)] = {**label, "ocr_sha1": entry.get("ocr_sha1", ""), "run": Path(run_dir).name}
    return pooled


def extracted_pairs(frame: pd.DataFrame) -> dict[tuple[str, str], dict[str, Any]]:
    """(page key, field) -> {pairs extracted, ocr_sha1} for every page-field of a candidate log."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for row in frame.to_dict("records"):
        key = (page_key(row["record_id"], row["page_number"], row["file_name"]), row["field"])
        slot = out.setdefault(key, {"found": [], "ocr_sha1": row["ocr_sha1"]})
        if str(row["is_placeholder"]) != "1" and row["value_norm"] and (_run_accepted(row) or _run_selected(row)):
            slot["found"].append(
                {
                    "candidate_id": row["candidate_id"],
                    "value": row["value"],
                    "value_norm": row["value_norm"],
                    "key_text": row["key_text"],
                    "region": row["region"],
                    "key_words": _ints(row["key_words"]),
                    "value_words": _ints(row["value_words"]),
                }
            )
    return out


def _same_ocr(label: dict[str, Any], ocr_hash: str) -> bool:
    return not (label.get("ocr_sha1") and ocr_hash and label["ocr_sha1"] != ocr_hash)


def _pair_totals(score: Counter) -> dict[str, Any]:
    return {
        "correct": score["right"],
        "wrong": score["wrong"],
        "missed": score["missed"],
        "total": score["right"] + score["wrong"] + score["missed"],
        "accuracy": pair_accuracy(score),
    }


def evaluate(run_dir: Path, pooled: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    """KV key-value pair accuracy of a run over every page-field that has a matching label:
    correct = right pairs, total = right + wrong + missed."""
    return evaluate_log(run_log(run_dir), pooled)


def evaluate_log(frame: pd.DataFrame, pooled: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    per_field: dict[str, Counter] = {field: Counter() for field in KV_FIELDS}
    for key, extracted in extracted_pairs(frame).items():
        label = pooled.get(key)
        field = key[1]
        if label is None or field not in per_field or not _same_ocr(label, extracted["ocr_sha1"]):
            continue
        per_field[field].update(score_pairs(extracted["found"], label))
        per_field[field]["page_fields"] += 1
    overall: Counter = sum(per_field.values(), Counter())
    return {
        **_pair_totals(overall),
        "page_fields": overall["page_fields"],
        "fields": {field: {**_pair_totals(score), "page_fields": score["page_fields"]} for field, score in per_field.items()},
    }


def _pct(part: int, whole: int) -> float | None:
    return round(100 * part / whole, 1) if whole else None


def evaluate_headings(run_dir: Path, pooled: dict[tuple[str, str], dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per heading detector: line-level precision / recall, level accuracy and exact pages.

    A selected heading is a true positive when a true heading on the page has the same text
    (normalized); its level is right when the reviewed level matches the predicted one.
    """
    frame = run_log(run_dir)
    frame = frame[frame["field"].isin(list(HEADING_FIELDS))]
    selected: dict[tuple[str, str], dict[str, Any]] = {}
    for row in frame.to_dict("records"):
        key = (page_key(row["record_id"], row["page_number"], row["file_name"]), row["field"])
        slot = selected.setdefault(key, {"ocr_sha1": row["ocr_sha1"], "norms": Counter(), "levels": {}})
        if _run_selected(row) and row["value_norm"]:
            slot["norms"][row["value_norm"]] += 1
            slot["levels"].setdefault(row["value_norm"], run_level(row))

    out = {field: {"tp": 0, "fp": 0, "fn": 0, "level_ok": 0, "pages": 0, "pages_exact": 0} for field in HEADING_FIELDS}
    for key, slot in selected.items():
        label = pooled.get(key)
        if label is None or not _same_ocr(label, slot["ocr_sha1"]):
            continue
        truth = Counter(item["value_norm"] for item in label.get("truth") or [] if item.get("value_norm"))
        levels: dict[str, str] = {}
        for item in label.get("truth") or []:
            levels.setdefault(item.get("value_norm", ""), item.get("level", ""))
        matched = slot["norms"] & truth
        tp = sum(matched.values())
        score = out[key[1]]
        score["tp"] += tp
        score["fp"] += sum(slot["norms"].values()) - tp
        score["fn"] += sum(truth.values()) - tp
        score["level_ok"] += sum(n for norm, n in matched.items() if slot["levels"].get(norm) == levels.get(norm))
        score["pages"] += 1
        score["pages_exact"] += int(slot["norms"] == truth)
    for score in out.values():
        score["precision"] = _pct(score["tp"], score["tp"] + score["fp"])
        score["recall"] = _pct(score["tp"], score["tp"] + score["fn"])
        score["level_accuracy"] = _pct(score["level_ok"], score["tp"])
        score["page_accuracy"] = _pct(score["pages_exact"], score["pages"])
    return out


def heading_pairs(score: dict[str, Any]) -> Counter:
    """A heading detector's lines as pairs, judged like key-value pairs: a detected heading is
    right when its text and its level are right, wrong when it is no true heading or has the
    wrong level, and a true heading nobody detected is missed."""
    return Counter(
        right=score["level_ok"],
        wrong=score["fp"] + score["tp"] - score["level_ok"],
        missed=score["fn"],
        page_fields=score["pages"],
    )


def evaluate_all(run_dir: Path, pooled: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    """Both extraction models of a run: the KV fields and the heading detectors, each as
    right / wrong / missed pairs; the overall accuracy adds them all up. 'kv' keeps the
    key-value subtotal, 'headings' the detectors' precision / recall / level details and, summed,
    their right / wrong / missed lines."""
    kv = evaluate(run_dir, pooled)
    headings = evaluate_headings(run_dir, pooled)
    fields = dict(kv["fields"])
    overall = Counter(right=kv["correct"], wrong=kv["wrong"], missed=kv["missed"], page_fields=kv["page_fields"])
    heading_total: Counter = Counter()
    heading_tp = heading_fp = heading_fn = 0
    for name, score in headings.items():
        pairs = heading_pairs(score)
        fields[name] = {**_pair_totals(pairs), "page_fields": pairs["page_fields"]}
        overall.update(pairs)
        heading_total.update(pairs)
        heading_tp += score["tp"]
        heading_fp += score["fp"]
        heading_fn += score["fn"]
    return {
        **_pair_totals(overall),
        "page_fields": overall["page_fields"],
        "fields": fields,
        "kv": {name: kv[name] for name in ("correct", "wrong", "missed", "total", "accuracy", "page_fields")},
        "heading": {
            **_pair_totals(heading_total),
            "page_fields": heading_total["page_fields"],
            "precision": _pct(heading_tp, heading_tp + heading_fp),
            "recall": _pct(heading_tp, heading_tp + heading_fn),
        },
        "headings": headings,
    }


def reviewed_fields(run_dir: Path, pooled: dict[tuple[str, str], dict[str, Any]] | None = None) -> dict[str, int]:
    """page key -> number of fields reviewed in this run, or (with pooled) reviewed in any run
    on the same OCR: a rerun of a reviewed batch is reviewed."""
    fields: dict[str, set[str]] = {}
    for key, entry in load_labels(run_dir)["pages"].items():
        fields[key] = set(entry["fields"])
    if pooled:
        frame = run_log(run_dir)
        if not frame.empty:
            pages = frame[["record_id", "page_number", "file_name", "field", "ocr_sha1"]].drop_duplicates()
            for row in pages.to_dict("records"):
                key = page_key(row["record_id"], row["page_number"], row["file_name"])
                label = pooled.get((key, row["field"]))
                if label is not None and _same_ocr(label, row["ocr_sha1"]):
                    fields.setdefault(key, set()).add(row["field"])
    return {key: len(names) for key, names in fields.items()}


def labels_signature(run_dirs: list[Path]) -> tuple[Any, ...]:
    """Changes whenever any run's reviews change (a cache key for the scores)."""
    out: list[Any] = []
    for run_dir in run_dirs:
        store = RunStore.get(run_dir)
        out.append((str(run_dir), store.version if store else 0))
    return tuple(out)

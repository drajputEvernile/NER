"""Second-pass person names from NER on patient-name key windows."""

from __future__ import annotations

import re

from .keys import key_windows
from .model import predict_entities
from ..rule_based.name_common import is_ignore, name_matches, tokenize

PERSON_LABELS = ["person"]
_CLEAN = re.compile(r"\s+")
_GENERIC = frozenset(
    {
        "patient",
        "the patient",
        "this patient",
        "tne patient",
        "new patient",
        "pt",
        "name",
        "patient name",
        "member name",
        "subscriber name",
        "preferred name",
        "caller name",
        "female",
        "male",
        "adult",
        "physician",
        "doctor",
        "caregiver",
        "patient caregiver",
        "old female",
        "dob",
        "mrn",
    }
)


def _clean(text: str) -> str:
    return _CLEAN.sub(" ", text).strip(" #.,;:|")


def _name_token_count(name: str) -> int:
    return sum(1 for token in tokenize(name) if token.isalpha() and not is_ignore(token))


def _is_full_name(name: str) -> bool:
    return 2 <= _name_token_count(name) <= 3


def _keep_hit(name: str) -> bool:
    if len(name) < 2 or not any(char.isalpha() for char in name):
        return False
    return name.casefold() not in _GENERIC


def _merge_person_hits(window: str, hits: list[dict]) -> list[tuple[float, str]]:
    spans: list[tuple[int, int, float, str]] = []
    for hit in hits:
        name = _clean(str(hit.get("text") or ""))
        if not _keep_hit(name):
            continue
        start = hit.get("start")
        end = hit.get("end")
        spans.append(
            (
                int(start) if start is not None else -1,
                int(end) if end is not None else -1,
                float(hit.get("score") or 0),
                name,
            )
        )
    spans.sort(key=lambda item: (item[0] if item[0] >= 0 else 10**9, item[1]))
    merged: list[tuple[int, int, float, str]] = []
    for start, end, score, name in spans:
        if not merged:
            merged.append((start, end, score, name))
            continue
        prev_start, prev_end, prev_score, prev_name = merged[-1]
        adjacent = start >= 0 and prev_end >= 0 and start <= prev_end + 3
        both_one = _name_token_count(prev_name) == 1 and _name_token_count(name) == 1
        if adjacent or (both_one and start >= 0 and prev_end >= 0 and start <= prev_end + 8):
            stop = max(prev_end, end)
            piece = _clean(window[prev_start:stop]) if prev_start >= 0 and stop > prev_start else ""
            joined = piece if _name_token_count(piece) >= 2 else _clean(f"{prev_name} {name}")
            merged[-1] = (prev_start, stop, max(prev_score, score), joined)
        else:
            merged.append((start, end, score, name))
    return [(score, name) for _start, _end, score, name in merged if _is_full_name(name)]


def extract_name_ner(
    ocr_text: str,
    first_name: str,
    last_name: str,
    middle_name: str = "",
    name_mode: str = "2",
    model_id: str | None = None,
) -> str:
    windows = key_windows(ocr_text, "patient_name")
    people: list[tuple[float, str]] = []
    for window in windows:
        people.extend(_merge_person_hits(window, predict_entities(window, PERSON_LABELS, model_id=model_id)))
    if not people:
        return "N/A"
    people.sort(key=lambda item: item[0], reverse=True)
    for _score, name in people:
        if name_matches(name, first_name, last_name, middle_name, name_mode):
            return name
    return people[0][1]

"""Read one DOB value from the sentence inside a key's overlay box."""

from __future__ import annotations

import re
from dataclasses import dataclass

from Util.dates import find_dates, normalize_date
from Util.keys import KeyHit
from Util.model import predict

LABELS = ["date", "date of birth"]
_CLEAN = re.compile(r"\s+")
_HAS_DIGIT = re.compile(r"\d")
_TIME_AFTER = re.compile(r"\s+\d{1,2}:\d{2}\b")
_FUZZY_DOB = re.compile(r"(?i)d[o0][bg]")


@dataclass
class DobHit:
    key: str
    region: str
    sentence: str
    ner_text: str = ""
    value: str = ""
    score: float = 0.0
    accepted: bool = False
    selected: bool = False
    source: str = "ner"


def _accept(text: str) -> str:
    cleaned = _CLEAN.sub(" ", text).strip(" #.,;:|")
    if not cleaned or not _HAS_DIGIT.search(cleaned):
        return ""
    return cleaned


def _key_span(sentence: str, key: str) -> tuple[int, int] | None:
    found = re.search(re.escape(key), sentence, flags=re.IGNORECASE)
    if found:
        return found.start(), found.end()
    if re.sub(r"[^a-z0-9]", "", key.casefold()) == "dob":
        fuzzy = _FUZZY_DOB.search(sentence)
        if fuzzy:
            return fuzzy.start(), fuzzy.end()
    return None


def _gap(sentence: str, key_at: tuple[int, int], date_at: tuple[int, int]) -> tuple[int, int, int]:
    """Word gap, then a penalty if a time follows the date, then character gap."""
    key_start, key_end = key_at
    date_start, date_end = date_at
    if date_end <= key_start:
        middle = sentence[date_end:key_start]
        char_gap = key_start - date_end
    elif date_start >= key_end:
        middle = sentence[key_end:date_start]
        char_gap = date_start - key_end
    else:
        middle = ""
        char_gap = 0
    followed_by_time = 1 if _TIME_AFTER.match(sentence[date_end:]) else 0
    return len(middle.split()), followed_by_time, char_gap


def nearest_date(sentence: str, key: str, raws: list[dict] | None = None) -> tuple[str, float]:
    """Date closest to the key (numeric or month-name). Time after the date loses a tie."""
    text = sentence or ""
    key_at = _key_span(text, key)
    dates = find_dates(text)
    if not dates or key_at is None:
        return "", 0.0
    chosen = min(dates, key=lambda match: _gap(text, key_at, (match.start(), match.end())))
    value = normalize_date(chosen.group(1))
    if not value:
        return "", 0.0
    score = 0.0
    for raw in raws or []:
        span = str(raw.get("text") or "")
        if value.casefold() in span.casefold() or chosen.group(0).casefold() in span.casefold():
            score = max(score, float(raw.get("score") or 0))
    if score <= 0:
        score = max(0.55, 0.92 - 0.12 * _gap(text, key_at, (chosen.start(), chosen.end()))[0])
    return value, round(score, 4)


def extract_box(hit: KeyHit) -> DobHit:
    """One overlay box produces one row."""
    raws = predict(hit.value_text, LABELS)
    value, score = nearest_date(hit.value_text, hit.key, raws)
    best_text = ""
    best_score = -1.0
    for raw in raws:
        ner_text = str(raw.get("text") or "").strip()
        accepted = _accept(ner_text)
        if not value or not accepted:
            continue
        if value.casefold() not in accepted.casefold() and accepted.casefold() not in value.casefold():
            continue
        raw_score = float(raw.get("score") or 0)
        if raw_score > best_score:
            best_text = ner_text
            best_score = raw_score
    if best_text:
        return DobHit(
            key=hit.key,
            region=hit.region,
            sentence=hit.value_text,
            ner_text=best_text,
            value=value,
            score=best_score,
            accepted=True,
            source="ner",
        )
    if value:
        return DobHit(
            key=hit.key,
            region=hit.region,
            sentence=hit.value_text,
            ner_text=value,
            value=value,
            score=score,
            accepted=True,
            source="geometry",
        )
    return DobHit(key=hit.key, region=hit.region, sentence=hit.value_text)


def mark_selected(rows: list[DobHit]) -> None:
    """On one page, the repeated nearest date wins. Highest score breaks a tie."""
    accepted = [row for row in rows if row.accepted and row.value]
    if not accepted:
        return
    counts: dict[str, int] = {}
    for row in accepted:
        counts[row.value.casefold()] = counts.get(row.value.casefold(), 0) + 1
    best_count = max(counts.values())
    tied = [row for row in accepted if counts[row.value.casefold()] == best_count]
    winner = max(tied, key=lambda row: (row.score, -len(row.value)))
    winner.selected = True

"""Read provider name + signature date from an electronic-signature key box.

Provider name uses the same rules as Provider_Name (credential suffix required).
Signature date is the nearest date/time stamp in the same overlay sentence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from Provider_Name.extract import (
    geometry_confidence,
    key_span,
    nearest_provider,
)
from Util.dates import find_dates, normalize_date
from Util.keys import KeyHit
from Util.model import predict
from Util.window import expand_for_key

LABELS = ["person", "date"]
_CLEAN = re.compile(r"\s+")


@dataclass
class ESigHit:
    key: str
    region: str
    sentence: str
    scale: str = ""
    ner_text: str = ""
    provider_name: str = ""
    signature_date: str = ""
    score: float = 0.0
    accepted: bool = False
    selected: bool = False
    source: str = "ner"


def _normalize_date(raw: str) -> str:
    return normalize_date(raw)


def _date_gap(
    sentence: str,
    key_at: tuple[int, int],
    date_at: tuple[int, int],
    after: int | None,
) -> tuple[int, int, int]:
    """Prefer dates after the provider name when known; then nearest to the key."""
    start, end = date_at
    after_penalty = 0
    if after is not None and start < after:
        after_penalty = 1
    key_start, key_end = key_at
    if end <= key_start:
        middle = sentence[end:key_start]
        char_gap = key_start - end
    elif start >= key_end:
        middle = sentence[key_end:start]
        char_gap = start - key_end
    else:
        middle = ""
        char_gap = 0
    return after_penalty, len(middle.split()), char_gap


def nearest_signature_date(
    sentence: str,
    key_at: tuple[int, int] | None,
    after: int | None = None,
) -> tuple[str, float]:
    text = sentence or ""
    if key_at is None:
        return "", 0.0
    matches = list(find_dates(text))
    if not matches:
        return "", 0.0
    chosen = min(
        matches,
        key=lambda match: _date_gap(text, key_at, (match.start(), match.end()), after),
    )
    value = normalize_date(chosen.group(1))
    if not value:
        return "", 0.0
    score = geometry_confidence(_date_gap(text, key_at, (chosen.start(), chosen.end()), after)[1])
    return value, score


def _name_end(sentence: str, provider_name: str) -> int | None:
    """Character offset after the provider name (+ credentials) in the sentence."""
    if not provider_name:
        return None
    found = re.search(re.escape(provider_name), sentence, flags=re.IGNORECASE)
    if found:
        return found.end()
    head = provider_name.split(",")[0].strip()
    found = re.search(re.escape(head), sentence, flags=re.IGNORECASE)
    return found.end() if found else None


def extract_box(hit: KeyHit) -> ESigHit:
    """One overlay box → provider name + signature date when present."""
    scale = f"{expand_for_key(hit.key):g}x"
    text = hit.value_text
    key_at = key_span(text, hit.value_words, hit.word_indexes)
    raws = predict(text, LABELS)

    provider, name_score, ner_text, source = nearest_provider(text, key_at, raws)
    after = _name_end(text, provider) if provider else None
    signature_date, date_score = nearest_signature_date(text, key_at, after=after)

    score = max(name_score, date_score)
    if provider or signature_date:
        return ESigHit(
            key=hit.key,
            region=hit.region,
            sentence=text,
            scale=scale,
            ner_text=ner_text or provider,
            provider_name=provider,
            signature_date=signature_date,
            score=score,
            accepted=bool(provider),
            source=source or ("geometry" if provider or signature_date else ""),
        )
    return ESigHit(key=hit.key, region=hit.region, sentence=text, scale=scale)


def mark_selected(rows: list[ESigHit]) -> None:
    """Prefer a full name+date hit; then highest score."""
    accepted = [row for row in rows if row.accepted and row.provider_name]
    if not accepted:
        return

    def rank(row: ESigHit) -> tuple[int, float, int]:
        both = 1 if row.provider_name and row.signature_date else 0
        return (both, row.score, -len(row.provider_name))

    winner = max(accepted, key=rank)
    winner.selected = True

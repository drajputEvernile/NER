"""Read one person name from the sentence inside a key's overlay box.

Accepted names are 2 to 4 words. One-word and 5+ word spans are rejected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from Util.keys import KeyHit
from Util.model import predict
from Util.geometry import KEYLESS_BAND_FRAC, boxes_overlap
from Util.window import expand_for_key, words_in_box

LABELS = ["person"]
_WORD = re.compile(r"[A-Za-z]+")
_CLEAN = re.compile(r"\s+")
_MIN_WORDS = 2
_MAX_WORDS = 4

# Keyless name: near a DOB or Member ID key only (either one is enough).
KEYLESS_TOP_FRAC = 0.20
KEYLESS_BOTTOM_FRAC = 0.10
ANCHOR_FIELDS = frozenset({"dob", "member_id"})

_TITLES = frozenset(
    {
        "jr",
        "sr",
        "ii",
        "iii",
        "iv",
        "md",
        "pa",
        "dr",
        "phd",
        "do",
        "rn",
        "np",
        "dds",
        "dmd",
        "mr",
        "mrs",
        "ms",
        "miss",
    }
)
_NON_NAME = frozenset(
    {
        "patient",
        "name",
        "ptnt",
        "pt",
        "the",
        "dob",
        "mrn",
        "date",
        "birth",
        "male",
        "female",
        "legal",
        "account",
        "record",
        "number",
        "member",
        "chart",
        "policy",
        "insurance",
        "id",
        "demographics",
        "gender",
        "identity",
        "admit",
        "discharge",
        "phone",
        "sex",
        "page",
        "refer",
        "doctor",
        "visit",
        "note",
        "of",
        "and",
        "for",
        "a",
        "preferred",
        "fin",
        "location",
        "position",
        "time",
        "status",
        "information",
        "service",
        "document",
        "primary",
        "care",
        "office",
        "perform",
        "auth",
        "result",
        "verified",
    }
)


@dataclass
class NameHit:
    key: str
    region: str
    sentence: str
    scale: str = ""
    ner_text: str = ""
    value: str = ""
    score: float = 0.0
    accepted: bool = False
    selected: bool = False
    source: str = "ner"


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text)


def accept(text: str) -> str:
    """Keep a 2-4 word person name. Reject anything shorter or longer."""
    tokens = _tokens(text)
    while tokens and tokens[0].casefold() in _TITLES | _NON_NAME:
        tokens.pop(0)
    while tokens and tokens[-1].casefold() in _TITLES:
        tokens.pop()
    if not tokens:
        return ""
    if any(token.casefold() in _NON_NAME for token in tokens):
        return ""
    name_tokens = [token for token in tokens if token.casefold() not in _TITLES]
    if not _MIN_WORDS <= len(name_tokens) <= _MAX_WORDS:
        return ""
    return _CLEAN.sub(" ", " ".join(tokens)).strip()


def key_span(sentence: str, words, indexes: list[int]) -> tuple[int, int] | None:
    """Character span of the key inside the overlay sentence."""
    del sentence
    wanted = set(indexes)
    offset = 0
    start = end = None
    for word in words:
        if offset:
            offset += 1
        if word.index in wanted:
            if start is None:
                start = offset
            end = offset + len(word.content)
        offset += len(word.content)
    if start is None:
        return None
    return start, end


def _word_gap(sentence: str, key_at: tuple[int, int], span_at: tuple[int, int]) -> tuple[int, int]:
    key_start, key_end = key_at
    span_start, span_end = span_at
    if span_end <= key_start:
        middle = sentence[span_end:key_start]
        after = 1
    elif span_start >= key_end:
        middle = sentence[key_end:span_start]
        after = 0
    else:
        middle = ""
        after = 0
    return len(middle.split()), after


def geometry_confidence(word_gap: int) -> float:
    return round(max(0.55, 0.92 - 0.12 * word_gap), 4)


def _candidate_spans(sentence: str) -> list[tuple[str, int, int]]:
    """Sliding windows of 2-4 name-like tokens in the sentence."""
    parts = sentence.split(" ")
    entries: list[tuple[str, int, int]] = []
    offset = 0
    for part in parts:
        if offset:
            offset += 1
        entries.append((part, offset, offset + len(part)))
        offset += len(part)

    found: list[tuple[str, int, int]] = []
    for start in range(len(entries)):
        for width in range(_MIN_WORDS, _MAX_WORDS + 1):
            end = start + width
            if end > len(entries):
                break
            chunk = " ".join(item[0] for item in entries[start:end])
            kept = accept(chunk)
            if not kept:
                continue
            found.append((kept, entries[start][1], entries[end - 1][2]))
    return found


def nearest(sentence: str, key_at: tuple[int, int] | None, raws: list[dict] | None = None) -> tuple[str, float, str]:
    """Person name closest to the key. Prefer NER spans; otherwise geometry near the key."""
    text = sentence or ""
    if key_at is None:
        return "", 0.0, ""

    ner_candidates: list[tuple[str, int, int, float]] = []
    for raw in raws or []:
        ner_text = str(raw.get("text") or "").strip()
        kept = accept(ner_text)
        if not kept:
            continue
        start = raw.get("start")
        end = raw.get("end")
        if not isinstance(start, int) or not isinstance(end, int):
            found = re.search(re.escape(ner_text), text, flags=re.IGNORECASE)
            if not found:
                continue
            start, end = found.start(), found.end()
        ner_candidates.append((kept, start, end, float(raw.get("score") or 0)))

    if ner_candidates:
        value, start, end, score = min(
            ner_candidates,
            key=lambda item: (*_word_gap(text, key_at, (item[1], item[2])), -item[3]),
        )
        if score <= 0:
            score = geometry_confidence(_word_gap(text, key_at, (start, end))[0])
        return value, round(score, 4), "ner"

    geometry = _candidate_spans(text)
    if not geometry:
        return "", 0.0, ""
    value, start, end = min(geometry, key=lambda item: _word_gap(text, key_at, (item[1], item[2])))
    score = geometry_confidence(_word_gap(text, key_at, (start, end))[0])
    return value, score, "geometry"


def extract_box(hit: KeyHit) -> NameHit:
    """One overlay box produces one row. The name is nearest the key and 2-4 words."""
    scale = f"{expand_for_key(hit.key):g}x"
    raws = predict(hit.value_text, LABELS)
    value, score, source = nearest(
        hit.value_text,
        key_span(hit.value_text, hit.value_words, hit.word_indexes),
        raws,
    )
    best_text = ""
    best_score = -1.0
    for raw in raws:
        ner_text = str(raw.get("text") or "").strip()
        accepted = accept(ner_text)
        if not value or not accepted or value.casefold() not in accepted.casefold():
            continue
        raw_score = float(raw.get("score") or 0)
        if raw_score > best_score:
            best_text = ner_text
            best_score = raw_score
    if best_text:
        return NameHit(
            key=hit.key,
            region=hit.region,
            sentence=hit.value_text,
            scale=scale,
            ner_text=best_text,
            value=value,
            score=best_score,
            accepted=True,
            source="ner",
        )
    if value:
        return NameHit(
            key=hit.key,
            region=hit.region,
            sentence=hit.value_text,
            scale=scale,
            ner_text=value,
            value=value,
            score=score,
            accepted=True,
            source=source or "geometry",
        )
    return NameHit(key=hit.key, region=hit.region, sentence=hit.value_text, scale=scale)


def mark_selected(rows: list[NameHit]) -> None:
    """On one page, the repeated nearest name wins. Highest score breaks a tie."""
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


def _anchor_zone(cy: float, page_h: float) -> str | None:
    """Top 20%, bottom 10%, or mid. None only if page height is missing."""
    if page_h <= 0:
        return None
    frac = cy / page_h
    if frac <= KEYLESS_TOP_FRAC:
        return "keyless_header"
    if frac >= 1.0 - KEYLESS_BOTTOM_FRAC:
        return "keyless_footer"
    return "keyless_mid"


def keyless_band(anchor: KeyHit, page_w: float, page_h: float) -> tuple[float, float, float, float]:
    """±KEYLESS_BAND_FRAC page height around the key, full page width horizontally."""
    pad_y = KEYLESS_BAND_FRAC * page_h if page_h else 8.0
    left = 0.0
    right = page_w if page_w else anchor.box.right + 200.0
    top = max(0.0, anchor.box.top - pad_y)
    bottom = min(page_h, anchor.box.bottom + pad_y) if page_h else anchor.box.bottom + pad_y
    return left, top, right, bottom


def extract_keyless(anchor: KeyHit, words, page_w: float, page_h: float) -> NameHit | None:
    """Name with no name key: must sit near this DOB or Member ID key."""
    if not anchor.trusted or anchor.field not in ANCHOR_FIELDS:
        return None
    zone = _anchor_zone(anchor.box.cy, page_h)
    if zone is None:
        return None
    band = keyless_band(anchor, page_w, page_h)
    band_words = words_in_box(words, band)
    if not band_words:
        return None
    sentence = " ".join(word.content for word in band_words).strip()
    if not sentence:
        return None
    raws = predict(sentence, LABELS)
    value, score, source = nearest(
        sentence,
        key_span(sentence, band_words, anchor.word_indexes),
        raws,
    )
    label = f"near:{anchor.key}"
    pct = f"±{KEYLESS_BAND_FRAC * 100:g}%"
    best_text = ""
    best_score = -1.0
    for raw in raws:
        ner_text = str(raw.get("text") or "").strip()
        accepted = accept(ner_text)
        if not value or not accepted or value.casefold() not in accepted.casefold():
            continue
        raw_score = float(raw.get("score") or 0)
        if raw_score > best_score:
            best_text = ner_text
            best_score = raw_score
    if best_text:
        return NameHit(
            key=label,
            region=zone,
            sentence=sentence,
            scale=pct,
            ner_text=best_text,
            value=value,
            score=best_score,
            accepted=True,
            source=f"keyless_{source or 'ner'}",
        )
    if value:
        return NameHit(
            key=label,
            region=zone,
            sentence=sentence,
            scale=pct,
            ner_text=value,
            value=value,
            score=score,
            accepted=True,
            source=f"keyless_{source or 'geometry'}",
        )
    return NameHit(key=label, region=zone, sentence=sentence, scale=pct, source="keyless")


def merge_page_names(keyed: list[NameHit], keyless: list[NameHit]) -> list[NameHit]:
    """Keep keyed rows. Drop keyless duplicates of a keyed accepted value."""
    known = {row.value.casefold() for row in keyed if row.accepted and row.value}
    extra: list[NameHit] = []
    seen_keyless: set[str] = set()
    for row in keyless:
        if row.accepted and row.value:
            text = row.value.casefold()
            if text in known or text in seen_keyless:
                continue
            seen_keyless.add(text)
        extra.append(row)
    return keyed + extra


def extract_page(hits: list[KeyHit], words, page_w: float, page_h: float) -> list[NameHit]:
    """Keyed pass, then keyless on every page except over keyed name boxes."""
    name_keys = [hit for hit in hits if hit.trusted and hit.field == "name" and hit.value_text]
    keyed = [extract_box(hit) for hit in name_keys]
    keyed_boxes = [hit.value_box for hit in name_keys if hit.value_box is not None]

    keyless: list[NameHit] = []
    for hit in hits:
        if not (hit.trusted and hit.field in ANCHOR_FIELDS):
            continue
        band = keyless_band(hit, page_w, page_h)
        if any(boxes_overlap(band, box) for box in keyed_boxes):
            continue
        row = extract_keyless(hit, words, page_w, page_h)
        if row is not None:
            keyless.append(row)

    rows = merge_page_names(keyed, keyless)
    mark_selected(rows)
    return rows

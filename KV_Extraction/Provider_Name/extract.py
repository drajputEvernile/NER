"""Read a provider name from the sentence inside a key's overlay box.

A hit is only accepted when a person name is followed by at least one known
credential suffix. Multiple suffixes may be separated by commas or periods.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from Util.keys import KeyHit
from Util.model import predict
from Util.window import expand_for_key

LABELS = ["person"]
_WORD = re.compile(r"[A-Za-z]+")
_CLEAN = re.compile(r"\s+")
_EDGE = re.compile(r"^[\s:,;\-|]+|[\s:,;\-|]+$")
_SPLIT = re.compile(r"[,]+|(?<=[A-Za-z0-9])\.(?=\s*[A-Za-z])")
_MIN_WORDS = 2
_MAX_WORDS = 4
_SUFFIXES_PATH = Path(__file__).resolve().parent / "suffixes.txt"

_NON_NAME = frozenset(
    {
        "provider",
        "physician",
        "doctor",
        "attending",
        "ordering",
        "primary",
        "care",
        "verified",
        "performed",
        "by",
        "the",
        "and",
        "for",
        "a",
        "of",
        "signature",
        "author",
        "resident",
        "surgeon",
        "pathologist",
        "psychologist",
        "anesthesiologist",
        "optometrist",
        "nurse",
        "practitioner",
        "patient",
        "name",
        "dob",
        "mrn",
        "date",
        "page",
        "pcp",
        "phys",
        "bill",
        "under",
        "referring",
        "medicare",
        "summa",
        "dermatology",
        "dermatologie",
        "dermatologic",
        "surgery",
        "assistant",
        "scribe",
        "signed",
        "electronically",
        "admission",
        "information",
        "internal",
        "medicine",
        "specialty",
        "wellness",
        "exam",
        "general",
        "meding",
        "certer",
        "center",
        "nertheast",
        "northeast",
        "akron",
        "street",
        "market",
        "work",
        "page",
        "fax",
        "location",
        "preop",
        "postop",
        "diagnosis",
    }
)


@dataclass
class ProviderHit:
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


def _norm_suffix(text: str) -> str:
    return re.sub(r"[\s.]+", "", text or "").casefold()


@lru_cache(maxsize=1)
def load_suffixes() -> dict[str, str]:
    """normalized form -> preferred display form from suffixes.txt."""
    mapping: dict[str, str] = {}
    if not _SUFFIXES_PATH.is_file():
        return mapping
    for raw in _SUFFIXES_PATH.read_text(encoding="utf-8").splitlines():
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        mapping[_norm_suffix(text)] = text
    return mapping


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text)


def accept_name(text: str) -> str:
    """Person name part only: 2-4 words, no label/credential junk."""
    catalog = load_suffixes()
    tokens = _tokens(text)
    while tokens and tokens[0].casefold() in _NON_NAME:
        tokens.pop(0)
    while tokens and tokens[-1].casefold() in _NON_NAME:
        tokens.pop()
    # Drop a leading/trailing token that is itself a credential (e.g. "MD").
    while tokens and _norm_suffix(tokens[0]) in catalog:
        tokens.pop(0)
    while tokens and _norm_suffix(tokens[-1]) in catalog:
        tokens.pop()
    if not tokens:
        return ""
    if any(token.casefold() in _NON_NAME for token in tokens):
        return ""
    if any(_norm_suffix(token) in catalog for token in tokens):
        return ""
    if not _MIN_WORDS <= len(tokens) <= _MAX_WORDS:
        return ""
    return _CLEAN.sub(" ", " ".join(tokens)).strip()


def key_span(sentence: str, words, indexes: list[int]) -> tuple[int, int] | None:
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


def _looks_like_credential(piece: str) -> bool:
    """Reject lowercase OCR noise (e.g. 'et' in 'Carter et Northeast')."""
    letters = [ch for ch in piece if ch.isalpha()]
    if not letters:
        return False
    # Real credentials are written with capitals (MD, PA-C, M.D., PharmD).
    return any(ch.isupper() for ch in letters)


def parse_trailing_suffixes(tail: str) -> str:
    """Read credential suffixes after the name. Separators may be , or ."""
    catalog = load_suffixes()
    if not catalog:
        return ""
    cleaned = _EDGE.sub("", tail or "")
    if not cleaned:
        return ""
    chunks = [part.strip() for part in _SPLIT.split(cleaned) if part.strip()]
    tokens: list[str] = []
    for chunk in chunks:
        tokens.extend(part for part in re.split(r"\s+", chunk) if part)

    collected: list[str] = []
    index = 0
    while index < len(tokens):
        matched: tuple[int, str] | None = None
        for width in range(min(4, len(tokens) - index), 0, -1):
            piece = " ".join(tokens[index : index + width])
            key = _norm_suffix(piece)
            if key in catalog and _looks_like_credential(piece):
                matched = (width, catalog[key])
                break
        if matched is None:
            break
        collected.append(matched[1])
        index += matched[0]
    if not collected:
        return ""
    # Drop immediate duplicates from OCR ("MD, MD").
    deduped: list[str] = []
    for item in collected:
        if not deduped or deduped[-1].casefold() != item.casefold():
            deduped.append(item)
    return ", ".join(deduped)


def with_suffixes(sentence: str, name: str, name_at: tuple[int, int]) -> str:
    """Return 'Name, MD, PA-C' when at least one known suffix follows the name."""
    start, end = name_at
    # Prefer the accepted name spelling; fall back to the span text.
    name_text = name or sentence[start:end]
    suffixes = parse_trailing_suffixes(sentence[end:])
    if not suffixes:
        return ""
    return f"{name_text}, {suffixes}"


def _name_span_in_sentence(sentence: str, name: str, hint_start: int | None = None) -> tuple[int, int] | None:
    if hint_start is not None and hint_start >= 0:
        return hint_start, hint_start + len(name)
    found = re.search(re.escape(name), sentence, flags=re.IGNORECASE)
    if not found:
        return None
    return found.start(), found.end()


def nearest_provider(
    sentence: str,
    key_at: tuple[int, int] | None,
    raws: list[dict] | None = None,
) -> tuple[str, float, str, str]:
    """Nearest person name that has a credential suffix after it."""
    text = sentence or ""
    if key_at is None:
        return "", 0.0, "", ""

    candidates: list[tuple[str, str, int, int, float, str]] = []
    for raw in raws or []:
        ner_text = str(raw.get("text") or "").strip()
        name = accept_name(ner_text)
        if not name:
            continue
        start = raw.get("start")
        end = raw.get("end")
        if not isinstance(start, int) or not isinstance(end, int):
            span = _name_span_in_sentence(text, ner_text)
            if span is None:
                continue
            start, end = span
        full = with_suffixes(text, name, (start, end))
        if not full:
            continue
        candidates.append((full, ner_text, start, end, float(raw.get("score") or 0), "ner"))

    if candidates:
        full, ner_text, start, end, score, source = min(
            candidates,
            key=lambda item: (*_word_gap(text, key_at, (item[2], item[3])), -item[4]),
        )
        if score <= 0:
            score = geometry_confidence(_word_gap(text, key_at, (start, end))[0])
        return full, round(score, 4), ner_text, source

    # Geometry: sliding name windows that are followed by a suffix.
    parts = text.split(" ")
    entries: list[tuple[str, int, int]] = []
    offset = 0
    for part in parts:
        if offset:
            offset += 1
        entries.append((part, offset, offset + len(part)))
        offset += len(part)
    geometry: list[tuple[str, str, int, int]] = []
    for start in range(len(entries)):
        for width in range(_MIN_WORDS, _MAX_WORDS + 1):
            end = start + width
            if end > len(entries):
                break
            chunk = " ".join(item[0] for item in entries[start:end])
            name = accept_name(chunk)
            if not name:
                continue
            span = (entries[start][1], entries[end - 1][2])
            full = with_suffixes(text, name, span)
            if full:
                geometry.append((full, name, span[0], span[1]))
    if not geometry:
        return "", 0.0, "", ""
    full, ner_text, start, end = min(geometry, key=lambda item: _word_gap(text, key_at, (item[2], item[3])))
    score = geometry_confidence(_word_gap(text, key_at, (start, end))[0])
    return full, score, ner_text, "geometry"


def extract_box(hit: KeyHit) -> ProviderHit:
    """One overlay box, one row. Provider only if a credential suffix follows the name."""
    scale = f"{expand_for_key(hit.key):g}x"
    raws = predict(hit.value_text, LABELS)
    value, score, ner_text, source = nearest_provider(
        hit.value_text,
        key_span(hit.value_text, hit.value_words, hit.word_indexes),
        raws,
    )
    if value:
        return ProviderHit(
            key=hit.key,
            region=hit.region,
            sentence=hit.value_text,
            scale=scale,
            ner_text=ner_text or value,
            value=value,
            score=score,
            accepted=True,
            source=source or "ner",
        )
    return ProviderHit(key=hit.key, region=hit.region, sentence=hit.value_text, scale=scale)


def mark_selected(rows: list[ProviderHit]) -> None:
    """On one page, the repeated provider value wins. Highest score breaks a tie."""
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

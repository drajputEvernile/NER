"""Read one member ID from the sentence inside a key's overlay box."""

from __future__ import annotations

import re
from dataclasses import dataclass

from Util.keys import KeyHit
from Util.model import predict

LABELS = ["ID", "identifier"]
_TOKEN = re.compile(r"^[A-Za-z0-9]+$")
_PHONE = re.compile(r"^\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}$")
_SLASH_DATE = re.compile(r"^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$")
_EDGE = re.compile(r"^[#.,;:|()\[\]]+|[#.,;:|()\[\]]+$")


@dataclass
class IdHit:
    key: str
    region: str
    sentence: str
    ner_text: str = ""
    value: str = ""
    score: float = 0.0
    accepted: bool = False
    selected: bool = False
    source: str = "ner"


def _normalize(text: str) -> str:
    return text.strip().strip("#.,;:|")


def accept(text: str) -> str:
    cleaned = _normalize(text)
    if not cleaned or any(char.isspace() for char in cleaned):
        return ""
    if not _TOKEN.fullmatch(cleaned) or _PHONE.fullmatch(text.strip()):
        return ""
    if len(cleaned) < 2:
        return ""
    return cleaned


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


def _word_gap(sentence: str, key_at: tuple[int, int], token_at: tuple[int, int]) -> tuple[int, int, int]:
    key_start, key_end = key_at
    token_start, token_end = token_at
    if token_end <= key_start:
        middle = sentence[token_end:key_start]
        after = 1
        char_gap = key_start - token_end
    elif token_start >= key_end:
        middle = sentence[key_end:token_start]
        after = 0
        char_gap = token_start - key_end
    else:
        middle = ""
        after = 0
        char_gap = 0
    return len(middle.split()), after, char_gap


def _id_token(raw: str) -> str:
    token = _EDGE.sub("", raw.strip())
    if not token or not any(char.isdigit() for char in token):
        return ""
    if _SLASH_DATE.fullmatch(token) or _PHONE.fullmatch(token):
        return ""
    if set(token.casefold()) <= {"x", "-"}:
        return ""
    kept = accept(token)
    if len(kept) < 4:
        return ""
    return kept


def geometry_confidence(word_gap: int) -> float:
    """A delivered geometry value is never 0. Adjacent to the key is 0.92."""
    return round(max(0.55, 0.92 - 0.12 * word_gap), 4)


def nearest(sentence: str, key_at: tuple[int, int] | None, raws: list[dict] | None = None) -> tuple[str, float]:
    """ID token closest to the key. A token after the key wins a tie. Leading zeros stay."""
    text = sentence or ""
    if key_at is None:
        return "", 0.0
    found = []
    offset = 0
    for part in text.split(" "):
        if offset:
            offset += 1
        token = _id_token(part)
        if token:
            found.append((token, offset, offset + len(part)))
        offset += len(part)
    if not found:
        return "", 0.0
    token, start, end = min(found, key=lambda item: _word_gap(text, key_at, (item[1], item[2])))
    word_gap = _word_gap(text, key_at, (start, end))[0]
    score = 0.0
    for raw in raws or []:
        span = str(raw.get("text") or "")
        if token.casefold() in span.casefold() or token.casefold() in _normalize(span).casefold():
            score = max(score, float(raw.get("score") or 0))
    if score <= 0:
        score = geometry_confidence(word_gap)
    return token, score


def extract_box(hit: KeyHit) -> IdHit:
    """One overlay box produces one row. The value is the ID nearest the key."""
    raws = predict(hit.value_text, LABELS)
    value, score = nearest(hit.value_text, key_span(hit.value_text, hit.value_words, hit.word_indexes), raws)
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
        return IdHit(
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
        return IdHit(
            key=hit.key,
            region=hit.region,
            sentence=hit.value_text,
            ner_text=value,
            value=value,
            score=score,
            accepted=True,
            source="geometry",
        )
    return IdHit(key=hit.key, region=hit.region, sentence=hit.value_text)


def keep_unique_keys(rows: list[IdHit]) -> list[IdHit]:
    """Every distinct key on the page. The same key keeps the higher confidence."""
    best: dict[str, IdHit] = {}
    for row in rows:
        current = best.get(row.key.casefold())
        if current is None or (row.accepted, row.score) > (current.accepted, current.score):
            best[row.key.casefold()] = row
    kept = list(best.values())
    for row in kept:
        row.selected = bool(row.accepted and row.value)
    return kept

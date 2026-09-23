"""Find catalog keys on an OCR page and decide which ones are trusted.

Each field keeps its own keys.json under KV_Extraction/{FieldFolder}/keys.json.
Missing key files are skipped so fields can be added one at a time.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .geometry import (
    WEAK_KEYS,
    Box,
    Word,
    median_height,
    near,
    near_keyless,
    region_of,
    union_boxes,
)
from .window import box_words

KV_ROOT = Path(__file__).resolve().parents[1]

# Folder name under KV_Extraction -> field id used in hits / window profiles.
FIELD_FOLDERS = {
    "dob": "Member_DOB",
    "member_id": "Member_ID",
    "name": "Member_Name",
    "provider_name": "Provider_Name",
    "dos": "DOS",
    "electronic_signature": "Electronic_Signature",
    "page_no": "Page_No",
}

_PARTS = re.compile(r"[a-z0-9]+")
_CATALOG: dict[str, list[str]] | None = None
_FUZZY = {
    "dob": re.compile(r"(?i)^d[o0][bg][:#.]?$"),
    "mrn": re.compile(r"(?i)^m[ar]n[:#.]?$"),
}

# Bare "Birth" is not DOB when followed by these, or when "age of first birth".
_DOB_BAD_AFTER = frozenset({"place", "order", "sex"})
# Bare "Name" is only a name key when the prior word is one of these (or absent).
_NAME_OK_BEFORE = frozenset({"legal", "preferred", "person", "patient"})


@dataclass
class KeyHit:
    field: str
    key: str
    word_indexes: list[int]
    box: Box
    weak: bool
    region: str = ""
    trusted: bool = False
    value_words: list[Word] = field(default_factory=list)
    value_box: Box | None = None
    value_text: str = ""


def _read_keys(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [str(item).strip() for item in data if str(item).strip()]
    if isinstance(data, dict):
        raw = data.get("keys")
        if isinstance(raw, list):
            return [str(item).strip() for item in raw if str(item).strip()]
    return []


def load_catalog() -> dict[str, list[str]]:
    """Load keys from each field folder that already has a keys.json."""
    global _CATALOG
    if _CATALOG is None:
        catalog: dict[str, list[str]] = {}
        for field_id, folder in FIELD_FOLDERS.items():
            path = KV_ROOT / folder / "keys.json"
            if not path.is_file():
                continue
            keys = _read_keys(path)
            if keys:
                catalog[field_id] = keys
        _CATALOG = catalog
    return _CATALOG


def reset_catalog() -> None:
    """Drop the cached key catalog (tests / hot-reload)."""
    global _CATALOG
    _CATALOG = None


def _parts(text: str) -> list[str]:
    return _PARTS.findall(text.casefold())


def _word_stream(words: list[Word]) -> list[tuple[str, int]]:
    stream: list[tuple[str, int]] = []
    for word in words:
        for part in _parts(word.content):
            stream.append((part, word.index))
    return stream


def _span_has_hash(words: list[Word], indexes: list[int]) -> bool:
    by_index = {word.index: word for word in words}
    return any("#" in by_index[index].content for index in indexes if index in by_index)


def _with_hash(words: list[Word], indexes: list[int], need_hash: bool) -> list[int] | None:
    if not need_hash:
        return indexes
    if _span_has_hash(words, indexes):
        return indexes
    positions = {word.index: position for position, word in enumerate(words)}
    last = positions.get(indexes[-1])
    if last is None or last + 1 >= len(words):
        return None
    nxt = words[last + 1]
    if "#" in nxt.content:
        return indexes + [nxt.index]
    return None


def _same_line(words: list[Word], indexes: list[int]) -> bool:
    selected = [word for word in words if word.index in set(indexes)]
    if len(selected) <= 1:
        return True
    height = median_height(selected)
    centers = [word.box.cy for word in selected]
    return max(centers) - min(centers) <= max(height * 0.8, 4.0)


def _neighbor_parts(words: list[Word], indexes: list[int]) -> tuple[list[str], list[str]]:
    """Token parts immediately before / after the matched key span."""
    positions = {word.index: position for position, word in enumerate(words)}
    first = positions.get(indexes[0])
    last = positions.get(indexes[-1])
    before: list[str] = []
    after: list[str] = []
    if first is not None and first > 0:
        before = _parts(words[first - 1].content)
    if last is not None and last + 1 < len(words):
        after = _parts(words[last + 1].content)
    # Also peek two words back for "age of first birth".
    if first is not None and first >= 3:
        before = _parts(words[first - 3].content) + _parts(words[first - 2].content) + before
    elif first is not None and first >= 2:
        before = _parts(words[first - 2].content) + before
    return before, after


def _dob_context_ok(words: list[Word], indexes: list[int], key: str) -> bool:
    """Reject Birth Place / Birth Order / Birth Sex / age of first birth."""
    parts = _parts(key)
    before, after = _neighbor_parts(words, indexes)
    # Only bare "Birth" (and fuzzy) is blocked by Place/Order/Sex.
    if parts == ["birth"] and after and after[0] in _DOB_BAD_AFTER:
        return False
    if "birth" in parts:
        tail = before[-3:] if len(before) >= 3 else before
        if tail == ["age", "of", "first"]:
            return False
        if "age" in before and "first" in before:
            return False
    return True


def _name_context_ok(words: list[Word], indexes: list[int], key: str) -> bool:
    """Bare Name only if prior word is Legal/Preferred/Person/Patient (or none)."""
    if _parts(key) != ["name"]:
        return True
    positions = {word.index: position for position, word in enumerate(words)}
    first = positions.get(indexes[0])
    if first is None or first <= 0:
        return True
    prior = _parts(words[first - 1].content)
    if not prior:
        return True
    return prior[-1] in _NAME_OK_BEFORE


def _fuzzy_matches(words: list[Word], key: str) -> list[list[int]]:
    """Whole-token OCR typos such as DOG for DOB, including dotted D.O.B keys."""
    pattern = _FUZZY.get("".join(_parts(key)))
    if pattern is None:
        return []
    return [[word.index] for word in words if pattern.fullmatch(word.content.strip())]


def _match_key(words: list[Word], stream: list[tuple[str, int]], key: str, field: str) -> list[list[int]]:
    parts = _parts(key)
    if not parts:
        return []
    need_hash = "#" in key
    found: list[list[int]] = []
    limit = len(stream) - len(parts) + 1
    for start in range(max(limit, 0)):
        if not all(stream[start + offset][0] == parts[offset] for offset in range(len(parts))):
            continue
        indexes: list[int] = []
        for offset in range(len(parts)):
            word_index = stream[start + offset][1]
            if not indexes or indexes[-1] != word_index:
                indexes.append(word_index)
        accepted = _with_hash(words, indexes, need_hash)
        if accepted is None or not _same_line(words, accepted):
            continue
        if field == "dob" and not _dob_context_ok(words, accepted, key):
            continue
        if field == "name" and not _name_context_ok(words, accepted, key):
            continue
        found.append(accepted)
    for indexes in _fuzzy_matches(words, key):
        if field == "dob" and not _dob_context_ok(words, indexes, key):
            continue
        found.append(indexes)
    return found


def _longest_first(catalog: dict[str, list[str]]) -> list[tuple[str, str]]:
    pairs = [(field, key) for field, keys in catalog.items() for key in keys]
    pairs.sort(key=lambda item: (len(_parts(item[1])), len(item[1])), reverse=True)
    return pairs


def find_key_hits(words: list[Word], page_w: float, page_h: float) -> list[KeyHit]:
    """All catalog keys, longest match first, with trust flags applied."""
    catalog = load_catalog()
    stream = _word_stream(words)
    by_index = {word.index: word for word in words}
    occupied: set[int] = set()
    hits: list[KeyHit] = []

    for field, key in _longest_first(catalog):
        for indexes in _match_key(words, stream, key, field):
            if any(index in occupied for index in indexes):
                continue
            key_words = [by_index[index] for index in indexes if index in by_index]
            box = union_boxes([word.box for word in key_words])
            if box is None:
                continue
            occupied.update(indexes)
            window = box_words(field, key, key_words, words, page_w, page_h)
            value_box = union_boxes([word.box for word in window])
            weak = key.casefold() in WEAK_KEYS or (
                field == "electronic_signature" and key.casefold() == "signature"
            )
            hits.append(
                KeyHit(
                    field=field,
                    key=key,
                    word_indexes=indexes,
                    box=box,
                    weak=weak,
                    region=region_of(box, value_box, page_h),
                    value_words=window,
                    value_box=value_box,
                    value_text=" ".join(word.content for word in window).strip(),
                )
            )
    _mark_trusted(hits, page_w, page_h)
    return hits


def _clusters(hits: list[KeyHit], page_w: float, page_h: float) -> list[list[int]]:
    parent = list(range(len(hits)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_right] = root_left

    for left in range(len(hits)):
        for right in range(left + 1, len(hits)):
            if near(hits[left].box, hits[right].box, page_w, page_h):
                union(left, right)
    groups: dict[int, list[int]] = {}
    for index in range(len(hits)):
        groups.setdefault(find(index), []).append(index)
    return list(groups.values())


def _band(region: str) -> str:
    if region in {"header", "footer"}:
        return region
    return "mid"


def _strong_in_same_band(hits: list[KeyHit], group: list[int], hit: KeyHit) -> bool:
    band = _band(hit.region)
    return any(not hits[index].weak and _band(hits[index].region) == band for index in group)


def _patient_has_nearby_key(hits: list[KeyHit], index: int, page_w: float, page_h: float) -> bool:
    """Patient is trusted only when a DOB/ID/other name key sits in the keyless band."""
    patient = hits[index]
    for other_index, other in enumerate(hits):
        if other_index == index:
            continue
        if other.field not in {"dob", "member_id", "name"}:
            continue
        if other.key.casefold() == "patient":
            continue
        if near_keyless(patient.box, other.box, page_w, page_h):
            return True
    return False


def _mark_trusted(hits: list[KeyHit], page_w: float, page_h: float) -> None:
    if not hits:
        return
    for group in _clusters(hits, page_w, page_h):
        clustered = len(group) >= 2
        for index in group:
            hit = hits[index]
            # Patient: require another key within keyless-band proximity.
            if hit.key.casefold() == "patient" and hit.field == "name":
                if not _patient_has_nearby_key(hits, index, page_w, page_h):
                    continue
                if hit.region == "mid":
                    hit.region = "mid_cluster"
                hit.trusted = True
                continue
            if hit.weak and not _strong_in_same_band(hits, group, hit):
                continue
            # Multi-word e-signature phrases are trusted even alone mid-page.
            if hit.field == "electronic_signature" and not hit.weak:
                if hit.region == "mid":
                    hit.region = "mid_cluster"
                hit.trusted = True
                continue
            if hit.region == "mid" and not clustered:
                continue
            if hit.region == "mid":
                hit.region = "mid_cluster"
            hit.trusted = True

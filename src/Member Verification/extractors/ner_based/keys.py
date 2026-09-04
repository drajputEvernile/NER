"""Find OCR token windows around Advantmed member-extraction keys."""

from __future__ import annotations

import json
from pathlib import Path

from ..rule_based.name_common import tokenize

HERE = Path(__file__).resolve().parent
KEY_GROUPS_PATH = HERE / "key_groups.json"
WINDOW = 5

_GROUPS: dict[str, list[str]] | None = None


def load_key_groups() -> dict[str, list[str]]:
    global _GROUPS
    if _GROUPS is None:
        data = json.loads(KEY_GROUPS_PATH.read_text(encoding="utf-8"))
        _GROUPS = {name: list(group.get("keys") or []) for name, group in data.items()}
    return _GROUPS


def keys_for(group: str) -> list[str]:
    return load_key_groups().get(group, [])


def _key_tokens(key: str) -> list[str]:
    return [token.casefold() for token in tokenize(key.replace(".", " "))]


def key_windows(ocr_text: str, group: str, radius: int = WINDOW) -> list[str]:
    tokens = tokenize(ocr_text)
    folded = [token.casefold() for token in tokens]
    windows: list[str] = []
    seen: set[tuple[int, int]] = set()
    for key in keys_for(group):
        needle = _key_tokens(key)
        if not needle:
            continue
        length = len(needle)
        for index in range(0, len(folded) - length + 1):
            if folded[index : index + length] != needle:
                continue
            start = max(0, index - radius)
            stop = min(len(tokens), index + length + radius)
            span = (start, stop)
            if span in seen:
                continue
            seen.add(span)
            windows.append(" ".join(tokens[start:stop]))
    return windows

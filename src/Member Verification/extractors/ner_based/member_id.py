"""Second-pass identifiers from GLiNER on member-id key windows."""

from __future__ import annotations

import re

from .keys import key_windows
from .model import predict_entities
from ..rule_based.member_id import extract_member_id

ID_LABELS = ["ID", "identifier", "medical record number"]


def extract_member_id_ner(ocr_text: str, member_id: str, model_id: str | None = None) -> str:
    value = (member_id or "").strip()
    if not value or value.upper() == "N/A":
        return "N/A"
    pattern = re.compile(r"(?<![A-Za-z0-9])" + re.escape(value) + r"(?![A-Za-z0-9])", re.IGNORECASE)
    windows = key_windows(ocr_text, "member_id")
    for window in windows:
        found = extract_member_id(window, value)
        if found != "N/A":
            return found
        for hit in predict_entities(window, ID_LABELS, model_id=model_id):
            text = str(hit.get("text") or "")
            if pattern.search(text):
                return value
    return "N/A"

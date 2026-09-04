"""Second-pass dates from GLiNER on date-of-birth key windows."""

from __future__ import annotations

from .keys import key_windows
from .model import predict_entities
from ..rule_based.dob import date_parts_match, extract_dob

DATE_LABELS = ["date", "date of birth"]


def extract_dob_ner(ocr_text: str, dummy_dob: str, model_id: str | None = None) -> str:
    dummy = (dummy_dob or "").strip()
    if not dummy or dummy.upper() == "N/A":
        return "N/A"
    windows = key_windows(ocr_text, "date_of_birth")
    for window in windows:
        found = extract_dob(window, dummy)
        if found != "N/A":
            return found
        for hit in predict_entities(window, DATE_LABELS, model_id=model_id):
            text = str(hit.get("text") or "")
            if date_parts_match(text, dummy):
                return dummy
    return "N/A"

"""Second-pass dates from GLiNER on the sentence around a date-of-birth key."""

from __future__ import annotations

from .keys import key_sentences
from .model import predict_entities
from ..rule_based.dob import date_parts_match, extract_dob

DATE_LABELS = ["date", "date of birth"]


def extract_dob_ner(ocr_text: str, dummy_dob: str, model_id: str | None = None) -> tuple[str, str]:
    """Return (detected_dob, key_used)."""
    dummy = (dummy_dob or "").strip()
    if not dummy or dummy.upper() == "N/A":
        return "N/A", ""
    for key, sentence in key_sentences(ocr_text, "date_of_birth"):
        found = extract_dob(sentence, dummy)
        if found != "N/A":
            return found, key
        for hit in predict_entities(sentence, DATE_LABELS, model_id=model_id, value_source=key):
            text = str(hit.get("text") or "")
            if date_parts_match(text, dummy):
                return dummy, key
    return "N/A", ""

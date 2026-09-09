"""Decide whether a page carries a *different* member than the one requested.

This drives the what-if rejection (5 pages or 10% of the pages with a wrong
member), so a false positive here can reject a whole chart. The signal is
therefore only taken from a patient-name context:

1. The people the NER pass recognised in the sentence around a patient-name
   key ("Patient Name: Maria Garcia"). If none of them verifies as the
   expected member, the page carries a wrong member.
2. Only when NER recognised nobody, the name written directly after a
   patient-name key, read from tokens. This keeps the rule working with no
   model loaded.

Names found anywhere else on the page are ignored: an attending physician,
signer or referring provider who happens to share the member's surname is not
evidence of a wrong member, and neither is a page with nothing on it. A value
of only initials plus one surname (``R S John``) is too thin to call, so it is
left to the NER pass.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from extractors.ner_based.keys import keys_for
from extractors.rule_based.name_common import (
    is_any_initial,
    is_ignore,
    is_label,
    name_matches,
    tokenize,
)

# Name tokens to read after a key, and full (non-initial) names needed before a
# value is treated as a nameable member.
MAX_VALUE_TOKENS = 4
MIN_FULL_TOKENS = 2

PATIENT_NAME_GROUP = "patient_name"


def _key_token_lists(group: str) -> list[list[str]]:
    out: list[list[str]] = []
    for key in keys_for(group):
        needle = [token.casefold() for token in tokenize(key.replace(".", " "))]
        if needle:
            out.append(needle)
    return out


def names_under_keys(ocr_text: str, group: str = PATIENT_NAME_GROUP) -> list[str]:
    """Return the name written right after each key of the group."""
    tokens = tokenize(ocr_text)
    folded = [token.casefold() for token in tokens]
    found: list[str] = []
    seen: set[int] = set()
    for needle in _key_token_lists(group):
        length = len(needle)
        for index in range(0, len(folded) - length + 1):
            if folded[index : index + length] != needle:
                continue
            start = index + length
            if start in seen:
                continue
            value: list[str] = []
            for token in tokens[start : start + MAX_VALUE_TOKENS]:
                if not token.isalpha() or is_label(token):
                    break
                value.append(token)
            full = [
                token
                for token in value
                if not is_any_initial(token) and not is_ignore(token)
            ]
            if len(full) >= MIN_FULL_TOKENS:
                seen.add(start)
                found.append(" ".join(value))
    return found


def wrong_member_on_page(
    ocr_text: str,
    expected: dict[str, str],
    name_mode: str,
    ner_names: Sequence[str] = (),
) -> bool:
    """True when a patient-name context on the page names another member.

    ``ner_names`` are the people the NER pass read out of the patient-name
    sentences on this page. When the model returned any of them they decide
    the answer: the page carries a wrong member when not one of them verifies
    as the expected member. Only when NER produced nothing (no model loaded,
    or no person recognised) does the key-anchored token read below stand in.
    """
    first = expected.get("DummyFirstName", "")
    middle = expected.get("DummyMiddleName", "")
    last = expected.get("DummyLastName", "")

    def matches(name: str) -> bool:
        return name_matches(name, first, last, middle, name_mode)

    names = [name for name in ner_names if name and name.strip()]
    if not names:
        names = names_under_keys(ocr_text)
    if not names:
        return False
    return not any(matches(name) for name in names)

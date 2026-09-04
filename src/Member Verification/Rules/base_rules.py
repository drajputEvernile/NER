"""Base member-verification rules.

1. Two of Name / DOB / Member ID must match to process a page.
2. Name is mandatory. DOB + Member ID without a name match is still Reject.
3. Details are checked on every page of the record.
4. System input is either a two-word name or a three-word name.
"""

from __future__ import annotations


def combine_evidences(
    *,
    name_ok: bool,
    dob_ok: bool,
    id_ok: bool,
    initial_only: bool = False,
) -> str:
    if not name_ok:
        return "Reject"
    if initial_only:
        return "Accept" if dob_ok and id_ok else "Reject"
    return "Accept" if dob_ok or id_ok else "Reject"


def is_present(value: str | None) -> bool:
    text = (value or "").strip()
    return bool(text) and text.upper() != "N/A"

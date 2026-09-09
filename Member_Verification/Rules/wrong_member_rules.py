"""Decide whether a page carries a *different* member than the one requested.

This drives the what-if rejection (5 pages or 10% of the pages with a wrong
member), and a wrong-member page rejects the whole chart, so the signal comes
from one place only: the people the NER model recognised in the sentence built
around a patient-name key ("Patient Name: Maria Garcia"). If none of them
verifies as the expected member, the page carries a wrong member.

Nothing here reads names itself. A token scan of the value after a key cannot
tell a person from prose -- it read "Patient Health Questionnaire" and "Patient
Care Team Lauren N" as members -- so judging person-hood is left to the model,
and a page where the model recognises nobody is simply not considered.

Names found outside a patient-name sentence are ignored too: an attending
physician, signer or referring provider who happens to share the member's
surname is not evidence of a wrong member.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from extractors.rule_based.name_common import name_matches


def wrong_member_on_page(
    ocr_text: str,
    expected: dict[str, str],
    name_mode: str,
    ner_names: Sequence[str] = (),
) -> bool:
    """True when a patient-name sentence on the page names another member.

    ``ner_names`` are the people the NER pass read out of this page's
    patient-name sentences. The page carries a wrong member when there is at
    least one of them and not one verifies as the expected member.
    """
    del ocr_text  # the NER pass has already read the page
    names = [name for name in ner_names if name and name.strip()]
    if not names:
        return False
    first = expected.get("DummyFirstName", "")
    middle = expected.get("DummyMiddleName", "")
    last = expected.get("DummyLastName", "")
    return not any(
        name_matches(name, first, last, middle, name_mode) for name in names
    )

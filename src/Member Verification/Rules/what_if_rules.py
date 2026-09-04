"""What-if rules for mixed correct / incorrect member pages in one record.

Delete allowance:
- 50 or more pages: at most 5 incorrect pages
- fewer than 50 pages: at most 10% incorrect pages

Move / split:
- incorrect member on the first 5 or last 5 pages → cannot Accept the record as-is

If incorrect pages exceed the delete allowance, the whole record is Reject.
"""

from __future__ import annotations

import math


def deletion_limit(total_pages: int) -> int:
    if total_pages >= 50:
        return 5
    return math.floor(total_pages * 0.10)


def is_edge_page(page_no: int, total_pages: int) -> bool:
    if total_pages <= 0:
        return False
    return page_no <= 5 or page_no > total_pages - 5


def apply_what_if(page_statuses: list[str], page_numbers: list[int], total_pages: int) -> list[str]:
    if not page_statuses:
        return page_statuses
    reject_indexes = [index for index, status in enumerate(page_statuses) if status != "Accept"]
    if not reject_indexes or len(reject_indexes) == len(page_statuses):
        return list(page_statuses)

    incorrect = len(reject_indexes)
    limit = deletion_limit(total_pages)
    if incorrect > limit or incorrect > 5:
        return ["Reject"] * len(page_statuses)

    for index in reject_indexes:
        if is_edge_page(int(page_numbers[index]), total_pages):
            return ["Reject"] * len(page_statuses)

    return list(page_statuses)

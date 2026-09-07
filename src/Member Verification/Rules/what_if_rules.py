"""What-if rules for mixed correct / incorrect member pages in one record.

Delete / move is not implemented yet. Until that is built:
- any incorrect page rejects the whole document
- no pages are deleted or moved

Later (not active now):
- Delete allowance: >=50 pages → at most 5; <50 pages → at most 10%
- Move / split: incorrect member on first 5 or last 5 pages
"""

from __future__ import annotations

import math


def deletion_limit(total_pages: int) -> int:
    """Reserved for future delete support."""
    if total_pages >= 50:
        return 5
    return math.floor(total_pages * 0.10)


def is_edge_page(page_no: int, total_pages: int) -> bool:
    """Reserved for future move / split support."""
    if total_pages <= 0:
        return False
    return page_no <= 5 or page_no > total_pages - 5


def apply_what_if(page_statuses: list[str], page_numbers: list[int], total_pages: int) -> list[str]:
    """Return document-level page statuses without delete or move.

    Delete/move will be added later. For now any Reject page means the
    whole document is Reject.
    """
    del page_numbers, total_pages  # unused until delete/move is implemented
    if not page_statuses:
        return page_statuses
    if all(status == "Accept" for status in page_statuses):
        return list(page_statuses)
    return ["Reject"] * len(page_statuses)

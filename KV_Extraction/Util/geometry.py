"""Page geometry for header, footer, and mid-page key clusters."""

from __future__ import annotations

from dataclasses import dataclass

HEADER_FRAC = 0.30
FOOTER_FRAC = 0.30
CLUSTER_Y_FRAC = 0.12
CLUSTER_X_FRAC = 0.40
# Keyless name band: ±5% page height (also used for Patient-key proximity).
KEYLESS_BAND_FRAC = 0.05

WEAK_KEYS = frozenset({"name", "patient"})


@dataclass(frozen=True)
class Box:
    left: float
    top: float
    right: float
    bottom: float

    @property
    def cx(self) -> float:
        return (self.left + self.right) / 2.0

    @property
    def cy(self) -> float:
        return (self.top + self.bottom) / 2.0

    def height(self) -> float:
        return max(0.0, self.bottom - self.top)

    def width(self) -> float:
        return max(0.0, self.right - self.left)


@dataclass
class Word:
    index: int
    content: str
    box: Box


def polygon_box(polygon: list) -> Box | None:
    if not polygon or len(polygon) < 8:
        return None
    xs = [float(value) for value in polygon[0::2]]
    ys = [float(value) for value in polygon[1::2]]
    return Box(min(xs), min(ys), max(xs), max(ys))


def union_boxes(boxes: list[Box]) -> Box | None:
    if not boxes:
        return None
    return Box(
        min(box.left for box in boxes),
        min(box.top for box in boxes),
        max(box.right for box in boxes),
        max(box.bottom for box in boxes),
    )


def page_size(page: dict, words: list[Word]) -> tuple[float, float]:
    width = float(page.get("width") or 0)
    height = float(page.get("height") or 0)
    if width > 0 and height > 0:
        return width, height
    bounds = union_boxes([word.box for word in words])
    if bounds is None:
        return 1.0, 1.0
    return max(bounds.right, 1.0), max(bounds.bottom, 1.0)


def words_from_page(page: dict) -> list[Word]:
    words: list[Word] = []
    for index, raw in enumerate(page.get("words") or []):
        if not isinstance(raw, dict):
            continue
        content = str(raw.get("content") or "").strip()
        box = polygon_box(raw.get("polygon") or [])
        if not content or box is None:
            continue
        words.append(Word(index=index, content=content, box=box))
    return words


def group_lines(words: list[Word]) -> list[list[Word]]:
    if not words:
        return []
    heights = sorted(word.box.height() for word in words if word.box.height() > 0)
    median = heights[len(heights) // 2] if heights else 10.0
    tolerance = max(median * 0.6, 1.0)
    ordered = sorted(words, key=lambda word: (word.box.cy, word.box.left))
    lines: list[list[Word]] = []
    current = [ordered[0]]
    for word in ordered[1:]:
        line_cy = sum(item.box.cy for item in current) / len(current)
        if abs(word.box.cy - line_cy) <= tolerance:
            current.append(word)
        else:
            lines.append(sorted(current, key=lambda item: item.box.left))
            current = [word]
    lines.append(sorted(current, key=lambda item: item.box.left))
    return lines


def median_height(words: list[Word]) -> float:
    heights = sorted(word.box.height() for word in words if word.box.height() > 0)
    if not heights:
        return 10.0
    return heights[len(heights) // 2]


def in_header(box: Box | None, page_h: float) -> bool:
    return box is not None and page_h > 0 and box.cy / page_h <= HEADER_FRAC


def in_footer(box: Box | None, page_h: float) -> bool:
    return box is not None and page_h > 0 and box.cy / page_h >= (1.0 - FOOTER_FRAC)


def region_of(key_box: Box, value_box: Box | None, page_h: float) -> str:
    """Header and footer win. The key position wins over the value box."""
    if in_header(key_box, page_h):
        return "header"
    if in_footer(key_box, page_h):
        return "footer"
    if in_header(value_box, page_h):
        return "header"
    if in_footer(value_box, page_h):
        return "footer"
    return "mid"


def near(left: Box, right: Box, page_w: float, page_h: float) -> bool:
    return (
        abs(left.cx - right.cx) <= CLUSTER_X_FRAC * page_w
        and abs(left.cy - right.cy) <= CLUSTER_Y_FRAC * page_h
    )


def near_keyless(left: Box, right: Box, page_w: float, page_h: float) -> bool:
    """True when boxes fall inside each other's keyless vertical band (full width)."""
    del page_w
    pad = KEYLESS_BAND_FRAC * page_h if page_h else 8.0
    left_top = left.top - pad
    left_bottom = left.bottom + pad
    right_top = right.top - pad
    right_bottom = right.bottom + pad
    return left_top <= right_bottom and right_top <= left_bottom


def boxes_overlap(left: tuple[float, float, float, float] | Box, right: Box | None) -> bool:
    """Axis-aligned overlap between a band tuple/box and a key value box."""
    if right is None:
        return False
    if isinstance(left, Box):
        a_left, a_top, a_right, a_bottom = left.left, left.top, left.right, left.bottom
    else:
        a_left, a_top, a_right, a_bottom = left
    return not (a_right < right.left or a_left > right.right or a_bottom < right.top or a_top > right.bottom)

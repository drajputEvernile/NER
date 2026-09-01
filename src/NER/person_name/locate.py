"""Map person-name spans back onto OCR bounding-box polygons."""

from __future__ import annotations

from typing import Any


def _page_height(document: dict) -> float:
    pages = document.get("pages") or {}
    if isinstance(pages, dict):
        first = next(iter(pages.values()), None)
    elif isinstance(pages, list) and pages:
        first = pages[0]
    else:
        first = None
    if not isinstance(first, dict):
        return 0.0
    size = first.get("size") or {}
    return float(size.get("height") or 0)


def bbox_to_polygon(bbox: dict, page_height: float = 0.0) -> list[list[float]]:
    left = float(bbox.get("l") or 0)
    top = float(bbox.get("t") or 0)
    right = float(bbox.get("r") or 0)
    bottom = float(bbox.get("b") or 0)
    origin = str(bbox.get("coord_origin") or "BOTTOMLEFT").upper()
    if origin == "BOTTOMLEFT" and page_height:
        top = page_height - top
        bottom = page_height - bottom
    return [
        [round(left, 2), round(top, 2)],
        [round(right, 2), round(top, 2)],
        [round(right, 2), round(bottom, 2)],
        [round(left, 2), round(bottom, 2)],
    ]


def union_polygons(polygons: list[list[list[float]]]) -> list[list[float]]:
    if not polygons:
        return []
    if len(polygons) == 1:
        return polygons[0]
    xs = [point[0] for poly in polygons for point in poly]
    ys = [point[1] for poly in polygons for point in poly]
    left, right = min(xs), max(xs)
    top, bottom = min(ys), max(ys)
    return [
        [round(left, 2), round(top, 2)],
        [round(right, 2), round(top, 2)],
        [round(right, 2), round(bottom, 2)],
        [round(left, 2), round(bottom, 2)],
    ]


def format_polygon(polygon: list[list[float]]) -> str:
    inner = ", ".join(f"[{x}, {y}]" for x, y in polygon)
    return f"[{inner}]"


def collect_text_boxes(ocr: dict) -> list[dict[str, Any]]:
    document = ocr.get("document") or {}
    page_h = _page_height(document)
    boxes: list[dict[str, Any]] = []

    for item in document.get("texts") or []:
        text = (item.get("text") or item.get("orig") or "").strip()
        if not text:
            continue
        for prov in item.get("prov") or []:
            bbox = prov.get("bbox")
            if not bbox:
                continue
            boxes.append({"text": text, "polygon": bbox_to_polygon(bbox, page_h)})

    for table in document.get("tables") or []:
        data = table.get("data") or {}
        for cell in data.get("table_cells") or []:
            text = (cell.get("text") or "").strip()
            bbox = cell.get("bbox")
            if not text or not bbox:
                continue
            boxes.append({"text": text, "polygon": bbox_to_polygon(bbox, page_h)})
    return boxes


def locate_name(
    content: str,
    name: str,
    boxes: list[dict[str, Any]],
    start: int | None = None,
    end: int | None = None,
) -> list[list[float]]:
    """Return one 4-point polygon for a name, or an empty list if none match."""
    if not name:
        return []

    snippet = ""
    if start is not None and end is not None and 0 <= start < end <= len(content):
        snippet = content[start:end]

    matches: list[list[list[float]]] = []
    if snippet:
        for box in boxes:
            text = box["text"]
            if text and text in snippet:
                matches.append(box["polygon"])
            elif text and snippet in text:
                matches.append(box["polygon"])
    if not matches:
        for box in boxes:
            text = box["text"]
            if not text:
                continue
            if name in text or text in name:
                matches.append(box["polygon"])
    return union_polygons(matches)

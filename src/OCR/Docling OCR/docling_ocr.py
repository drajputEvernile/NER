"""Docling OCR with on-disk JSON cache for the rest of the pipeline."""
from __future__ import annotations

import json
import logging
import math
import sys
import threading
from pathlib import Path
from time import perf_counter

from PIL import Image

logger = logging.getLogger(__name__)

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from env_loader import load_sibling_config

_cfg = load_sibling_config(__file__, "docling_ocr_folder_config")
enabled = bool(_cfg.enabled)
RAW_Read_Path = _cfg.RAW_Read_Path
Docling_OCR_Output_path = _cfg.Docling_OCR_Output_path
record_output_dir = _cfg.record_output_dir

_CONVERT_LOCK = threading.Lock()
_CONVERTER = None


def _bbox_polygon(bbox, *, sx: float, sy: float, img_h: int) -> list[float]:
    l = float(getattr(bbox, "l", 0.0)) * sx
    t = float(getattr(bbox, "t", 0.0)) * sy
    r = float(getattr(bbox, "r", 0.0)) * sx
    b = float(getattr(bbox, "b", 0.0)) * sy
    origin = str(getattr(bbox, "coord_origin", "") or "").upper().replace("-", "")
    if origin in {"BOTTOMLEFT", "BOTTOMLEFTED"}:
        t, b = img_h - t, img_h - b
        t, b = min(t, b), max(t, b)
    return [round(v, 2) for v in (l, t, r, t, r, b, l, b)]


def _cell_polygon(cell, *, sx: float, sy: float, img_h: int) -> list[float]:
    rect = getattr(cell, "rect", None)
    if rect is not None:
        coords: list[float] = []
        for i in range(4):
            x = getattr(rect, f"r_x{i}", None)
            y = getattr(rect, f"r_y{i}", None)
            if x is None or y is None:
                coords = []
                break
            coords.extend([float(x) * sx, float(y) * sy])
        if len(coords) == 8:
            return [round(v, 2) for v in coords]
        if hasattr(rect, "to_bounding_box"):
            try:
                return _bbox_polygon(rect.to_bounding_box(), sx=sx, sy=sy, img_h=img_h)
            except Exception:
                pass
    bbox = getattr(cell, "bbox", None) or getattr(cell, "bounding_box", None)
    if bbox is not None:
        return _bbox_polygon(bbox, sx=sx, sy=sy, img_h=img_h)
    return []


def _cell_text(cell) -> str:
    return (getattr(cell, "text", None) or getattr(cell, "orig", None) or "").strip()


def _cell_confidence(cell) -> float | None:
    """OCR engine score on a Docling TextCell, if present. No invented 100%."""
    if getattr(cell, "from_ocr", None) is False:
        return None
    raw = getattr(cell, "confidence", None)
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value > 1.0:
        value = value / 100.0
    if value < 0.0:
        return None
    return max(0.0, min(1.0, value))


def _ocr_item(text: str, poly: list[float], confidence: float | None) -> dict:
    item: dict = {"content": text, "polygon": poly}
    if confidence is not None:
        item["confidence"] = round(confidence, 4)
    return item


def _merge_line(words: list[dict]) -> dict:
    words = sorted(words, key=lambda w: min(w["polygon"][0::2]))
    polys = [w["polygon"] for w in words if w.get("polygon")]
    left = min(min(p[0::2]) for p in polys)
    top = min(min(p[1::2]) for p in polys)
    right = max(max(p[0::2]) for p in polys)
    bottom = max(max(p[1::2]) for p in polys)
    confs = [float(w["confidence"]) for w in words if w.get("confidence") is not None]
    return {
        "content": " ".join(w["content"] for w in words),
        "polygon": [left, top, right, top, right, bottom, left, bottom],
        **({"confidence": round(sum(confs) / len(confs), 4)} if confs else {}),
    }


def _cluster_words_to_lines(words: list[dict]) -> list[dict]:
    if not words:
        return []
    heights = [max(w["polygon"][1::2]) - min(w["polygon"][1::2]) for w in words]
    median_h = sorted(heights)[len(heights) // 2] if heights else 12.0
    thresh = max(8.0, float(median_h) * 0.6)
    items = []
    for word in words:
        ys = word["polygon"][1::2]
        items.append((sum(ys) / 4.0, min(word["polygon"][0::2]), word))
    items.sort(key=lambda t: (t[0], t[1]))
    lines: list[dict] = []
    current: list[dict] = []
    current_y: float | None = None
    for y, _x, word in items:
        if current_y is None or abs(y - current_y) <= thresh:
            current.append(word)
            current_y = y if current_y is None else (current_y * 0.7 + y * 0.3)
        else:
            lines.append(_merge_line(current))
            current = [word]
            current_y = y
    if current:
        lines.append(_merge_line(current))
    return lines


def _estimate_page_angle(words: list[dict]) -> float:
    angles: list[float] = []
    for word in words:
        poly = word.get("polygon") or []
        if len(poly) < 8:
            continue
        pts = list(zip(poly[0::2], poly[1::2]))
        left = min(pts, key=lambda p: p[0])
        right = max(pts, key=lambda p: p[0])
        dx = right[0] - left[0]
        if abs(dx) < 2:
            continue
        angles.append(math.degrees(math.atan2(right[1] - left[1], dx)))
    if not angles:
        return 0.0
    angles.sort()
    return round(float(angles[len(angles) // 2]), 2)


def _scale_for_page(page, parsed, img_w: int, img_h: int) -> tuple[float, float]:
    page_w, page_h = float(img_w), float(img_h)
    dim = getattr(parsed, "dimension", None) if parsed is not None else None
    size = getattr(page, "size", None)
    for src in (dim, size):
        if src is None:
            continue
        width = float(getattr(src, "width", 0) or 0)
        height = float(getattr(src, "height", 0) or 0)
        if width > 1 and height > 1:
            page_w, page_h = width, height
            break
    sx = img_w / page_w if page_w else 1.0
    sy = img_h / page_h if page_h else 1.0
    return sx, sy


def _unique_cells(cells) -> list:
    seen: set = set()
    unique: list = []
    for cell in cells:
        key = getattr(cell, "index", None)
        if key is None:
            key = id(cell)
        if key in seen:
            continue
        seen.add(key)
        unique.append(cell)
    unique.sort(key=lambda c: int(getattr(c, "index", 0) or 0))
    return unique


def _parsed_page(page):
    parsed = getattr(page, "parsed_page", None)
    if parsed is None and hasattr(page, "get_segmented_page"):
        try:
            parsed = page.get_segmented_page()
        except Exception:
            parsed = None
    return parsed


def _page_cells(page) -> list:
    try:
        return list(getattr(page, "cells", None) or [])
    except Exception:
        return []


def _layout_ocr_cells(page) -> list:
    clusters = []
    layout = getattr(getattr(page, "predictions", None), "layout", None)
    clusters.extend(list(getattr(layout, "clusters", None) or []))
    assembled = getattr(page, "assembled", None)
    if assembled is not None:
        for element in list(getattr(assembled, "elements", None) or []):
            cluster = getattr(element, "cluster", None)
            if cluster is not None:
                clusters.append(cluster)
    cells = []
    for cluster in clusters:
        cells.extend(list(getattr(cluster, "cells", None) or []))
    return _unique_cells(cells)


def _items_from_cells(cells, *, sx: float, sy: float, img_h: int) -> list[dict]:
    items: list[dict] = []
    for cell in cells:
        text = _cell_text(cell)
        poly = _cell_polygon(cell, sx=sx, sy=sy, img_h=img_h)
        if text and len(poly) == 8:
            items.append(_ocr_item(text, poly, _cell_confidence(cell)))
    return items


def _cells_from_result(conv_result, img_w: int, img_h: int) -> tuple[list[dict], list[dict]]:
    words: list[dict] = []
    lines: list[dict] = []
    pages = list(getattr(conv_result, "pages", None) or [])
    for page in pages:
        parsed = _parsed_page(page)
        sx, sy = _scale_for_page(page, parsed, img_w, img_h)
        word_cells = _unique_cells(list(getattr(parsed, "word_cells", None) or [])) if parsed is not None else []
        line_cells = _unique_cells(list(getattr(parsed, "textline_cells", None) or [])) if parsed is not None else []
        if not line_cells:
            line_cells = _unique_cells(_page_cells(page))
        if not word_cells and not line_cells:
            line_cells = _layout_ocr_cells(page)
        ocr_words = _items_from_cells(word_cells, sx=sx, sy=sy, img_h=img_h)
        ocr_lines = _items_from_cells(line_cells, sx=sx, sy=sy, img_h=img_h)
        if ocr_words:
            words.extend(ocr_words)
        elif ocr_lines:
            words.extend(ocr_lines)
        if ocr_lines:
            lines.extend(ocr_lines)
        elif ocr_words:
            lines.extend(_cluster_words_to_lines(ocr_words))

    if not words and not lines:
        doc = getattr(conv_result, "document", None)
        if doc is not None and hasattr(doc, "iterate_items"):
            for item, _level in doc.iterate_items():
                text = (getattr(item, "text", None) or "").strip()
                prov = getattr(item, "prov", None) or []
                if not text or not prov:
                    continue
                bbox = getattr(prov[0], "bbox", None)
                if bbox is None:
                    continue
                poly = _bbox_polygon(
                    bbox, sx=img_w / max(float(img_w), 1), sy=img_h / max(float(img_h), 1), img_h=img_h
                )
                size = getattr(getattr(doc, "pages", {}).get(getattr(prov[0], "page_no", 1), None), "size", None)
                if size is not None:
                    pw = float(getattr(size, "width", img_w) or img_w)
                    ph = float(getattr(size, "height", img_h) or img_h)
                    poly = _bbox_polygon(bbox, sx=img_w / pw, sy=img_h / ph, img_h=img_h)
                lines.append(_ocr_item(text, poly, None))

    if not lines and words:
        lines = _cluster_words_to_lines(words)
    if not words and lines:
        words = [dict(line) for line in lines]
    n_conf = sum(1 for w in words if w.get("confidence") is not None)
    logger.info("event=docling_ocr_cells n_words=%s n_with_conf=%s", len(words), n_conf)
    return words, lines


def _build_converter():
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, ImageFormatOption

    opts = PdfPipelineOptions()
    opts.do_ocr = True
    opts.do_table_structure = False
    opts.images_scale = 1.0
    try:
        from docling.datamodel.pipeline_options import OcrMode

        opts.ocr_options.mode = OcrMode.FULL_PAGE
    except Exception:
        pass
    logger.info(
        "event=docling_ocr_options type=%s mode=%s",
        type(opts.ocr_options).__name__,
        getattr(opts.ocr_options, "mode", None),
    )
    return DocumentConverter(
        allowed_formats=[InputFormat.IMAGE],
        format_options={InputFormat.IMAGE: ImageFormatOption(pipeline_options=opts)},
    )


def _converter():
    global _CONVERTER
    if _CONVERTER is None:
        with _CONVERT_LOCK:
            if _CONVERTER is None:
                logger.info("event=docling_converter_init")
                _CONVERTER = _build_converter()
                logger.info("event=docling_converter_ready")
    return _CONVERTER


def _run_docling(image_path: Path) -> dict:
    with Image.open(image_path) as img:
        img_w, img_h = img.size
    conv = _converter()
    logger.info("event=docling_convert_start path=%s", image_path)
    with _CONVERT_LOCK:
        result = conv.convert(str(image_path))
    logger.info("event=docling_convert_done path=%s", image_path)
    words, lines = _cells_from_result(result, img_w, img_h)
    content = "\n".join(line["content"] for line in lines if line.get("content"))
    angle = _estimate_page_angle(words)
    return {
        "content": content,
        "model": "docling",
        "pages": [
            {
                "pageNumber": 1,
                "angle": angle,
                "width": img_w,
                "height": img_h,
                "unit": "pixel",
                "lines": lines,
                "words": words,
            }
        ],
    }


class DoclingOcrExtractor:
    @property
    def available(self) -> bool:
        return True

    def extract_page_outputs(
        self, image_path: Path, output_dir: Path, *, cache_stem: str | None = None, force: bool = False
    ) -> dict:
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = cache_stem or image_path.stem
        json_path = output_dir / f"{stem}.json"
        text_path = output_dir / f"{stem}.txt"
        if not force and json_path.is_file():
            logger.info("event=docling_ocr_cache_hit stem=%s path=%s", stem, json_path)
            return {
                "model": "docling",
                "features": {"barcodes": False, "languages": True},
                "outputs": {
                    "json": str(json_path),
                    "text": str(text_path) if text_path.is_file() else "",
                },
                "cached": True,
            }

        started = perf_counter()
        json_result = _run_docling(image_path)
        elapsed = round(perf_counter() - started, 3)
        with json_path.open("w", encoding="utf-8") as f:
            json.dump(json_result, f, indent=2)
        text_path.write_text(json_result.get("content", "") or "", encoding="utf-8")
        return {
            "model": "docling",
            "features": {"barcodes": False, "languages": True},
            "docling_ocr_seconds": elapsed,
            "outputs": {"json": str(json_path), "text": str(text_path)},
        }

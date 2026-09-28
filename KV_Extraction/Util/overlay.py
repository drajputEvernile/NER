"""Draw trusted key boxes and their field overlay windows onto raw page images."""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from Util import config
from Util.geometry import (
    FOOTER_FRAC,
    HEADER_FRAC,
    KEYLESS_BAND_FRAC,
    boxes_overlap,
)
from Util.window import expand_for_key, field_box

logger = logging.getLogger(__name__)

_COLORS = (
    (220, 38, 38),
    (22, 163, 74),
    (37, 99, 235),
    (217, 119, 6),
    (124, 58, 237),
    (8, 145, 178),
    (190, 24, 93),
    (101, 163, 13),
    (79, 70, 229),
    (194, 65, 12),
)

_FIELD_COLORS = {
    "dob": (220, 38, 38),
    "member_id": (22, 163, 74),
    "name": (37, 99, 235),
    "provider_name": (217, 119, 6),
    "electronic_signature": (124, 58, 237),
    "page_no": (8, 145, 178),
    "dos": (190, 24, 93),
}

_FIELD_ORDER = (
    "dob",
    "member_id",
    "name",
    "provider_name",
    "electronic_signature",
    "dos",
)

_NO_KEY_LABELS = {
    "dob": "no DOB key",
    "member_id": "no member ID key",
    "name": "no name key",
    "provider_name": "no provider key",
    "electronic_signature": "no e-signature key",
    "dos": "no DOS key",
}


def _scale(box, image_size, page_w: float, page_h: float) -> tuple[int, int, int, int]:
    width, height = image_size
    sx = width / page_w if page_w else 1.0
    sy = height / page_h if page_h else 1.0
    return (
        int(box[0] * sx),
        int(box[1] * sy),
        int(box[2] * sx),
        int(box[3] * sy),
    )


def _draw_hit(draw, hit, image_size, page_w: float, page_h: float, color, font) -> None:
    key_box = _scale(
        (hit.box.left, hit.box.top, hit.box.right, hit.box.bottom),
        image_size,
        page_w,
        page_h,
    )
    scale = expand_for_key(hit.key)
    window = field_box(
        hit.field,
        hit.key,
        hit.box.left,
        hit.box.top,
        hit.box.right,
        hit.box.bottom,
        page_w,
        page_h,
    )
    wide = _scale(window, image_size, page_w, page_h)
    draw.rectangle(wide, outline=color + (255,), width=4)
    draw.rectangle(wide, fill=color + (36,))
    draw.rectangle(key_box, outline=color + (255,), width=3)
    label = f"{hit.field}:{hit.key} {scale:g}x"
    text_x = max(2, wide[0] + 2)
    text_y = max(2, wide[1] + 2)
    draw.rectangle(
        (text_x, text_y, text_x + 8 * len(label) + 6, text_y + 14),
        fill=(255, 255, 255, 210),
    )
    draw.text((text_x + 2, text_y), label, fill=color + (255,), font=font)


def _draw_keyless(draw, all_hits, image_size, page_w: float, page_h: float, font, color_index: int = 0) -> int:
    from Member_Name.extract import ANCHOR_FIELDS, _anchor_zone, keyless_band

    name_boxes = [hit.value_box for hit in all_hits if hit.trusted and hit.field == "name" and hit.value_box]
    for anchor in all_hits:
        if not (anchor.trusted and anchor.field in ANCHOR_FIELDS):
            continue
        # Keyless overlays only in header/footer — never mid-page.
        if _anchor_zone(anchor.box.cy, page_h) is None:
            continue
        band = keyless_band(anchor, page_w, page_h)
        if any(boxes_overlap(band, box) for box in name_boxes):
            continue
        color = _COLORS[color_index % len(_COLORS)]
        color_index += 1
        wide = _scale(band, image_size, page_w, page_h)
        draw.rectangle(wide, outline=color + (180,), width=2)
        draw.rectangle(wide, fill=color + (24,))
        label = f"keyless±{KEYLESS_BAND_FRAC * 100:g}% near {anchor.key}"
        text_x = max(2, wide[0] + 2)
        text_y = max(2, wide[1] + 2)
        draw.rectangle(
            (text_x, text_y, text_x + 8 * len(label) + 6, text_y + 14),
            fill=(255, 255, 255, 210),
        )
        draw.text((text_x + 2, text_y), label, fill=color + (255,), font=font)
    return color_index


def _draw_page_labels(draw, labels, image_size, page_w: float, page_h: float, font, *, bands: bool) -> int:
    """Box every printed page label; optionally shade the header/footer search bands."""
    color = _FIELD_COLORS["page_no"]
    if bands and page_h > 0:
        for top, bottom in ((0.0, HEADER_FRAC * page_h), ((1.0 - FOOTER_FRAC) * page_h, page_h)):
            band = _scale((0.0, top, page_w, bottom), image_size, page_w, page_h)
            draw.rectangle(band, fill=color + (18,))
    boxed = [hit for hit in labels if hit.box is not None]
    for hit in boxed:
        box = _scale((hit.box.left, hit.box.top, hit.box.right, hit.box.bottom), image_size, page_w, page_h)
        draw.rectangle(box, outline=color + (255,), width=3)
        label = f"page_no:{hit.page_no}" + (f"/{hit.page_total}" if hit.page_total else "")
        text_y = max(2, box[1] - 16)
        draw.rectangle(
            (box[0], text_y, box[0] + 8 * len(label) + 6, text_y + 14),
            fill=(255, 255, 255, 210),
        )
        draw.text((box[0] + 2, text_y), label, fill=color + (255,), font=font)
    return len(boxed)


def _draw_field(draw, field: str, hits, labels, image_size, page_w: float, page_h: float, font) -> None:
    """One field's overlay: its trusted keys and windows (page_no: labels + bands)."""
    if field == "page_no":
        if not _draw_page_labels(draw, labels, image_size, page_w, page_h, font, bands=True):
            draw.text((8, 8), "no page label", fill=(220, 38, 38, 255), font=font)
        return
    color_index = 0
    if field == "name":
        color_index = _draw_keyless(draw, hits, image_size, page_w, page_h, font, color_index)
    field_hits = [hit for hit in hits if hit.trusted and hit.field == field]
    for hit in field_hits:
        color = _FIELD_COLORS.get(field, _COLORS[color_index % len(_COLORS)])
        color_index += 1
        _draw_hit(draw, hit, image_size, page_w, page_h, color, font)
    if not field_hits and field != "name":
        draw.text((8, 8), _NO_KEY_LABELS.get(field, "no key"), fill=(220, 38, 38, 255), font=font)


def _draw_overall(draw, hits, labels, image_size, page_w: float, page_h: float, font) -> None:
    """Every field on one image."""
    color_index = _draw_keyless(draw, hits, image_size, page_w, page_h, font, 0)
    any_hit = False
    for field in _FIELD_ORDER:
        color = _FIELD_COLORS.get(field, _COLORS[color_index % len(_COLORS)])
        for hit in hits:
            if not (hit.trusted and hit.field == field):
                continue
            any_hit = True
            _draw_hit(draw, hit, image_size, page_w, page_h, color, font)
            color_index += 1
    if _draw_page_labels(draw, labels, image_size, page_w, page_h, font, bands=False):
        any_hit = True
    if not any_hit:
        draw.text((8, 8), "no trusted keys", fill=(220, 38, 38, 255), font=font)


def _save_page(image, layer, out_dir: Path, record_id: str, page: dict, file_name: str) -> Path:
    marked = Image.alpha_composite(image, layer).convert("RGB")
    page_number = int(page.get("pageNumber") or 0)
    record_dir = out_dir / record_id
    record_dir.mkdir(parents=True, exist_ok=True)
    dest = record_dir / f"page_{page_number:02d}_{file_name}"
    marked.save(dest)
    return dest


def render_page_overlays(
    record_id: str,
    page: dict,
    hits,
    labels,
    page_w: float,
    page_h: float,
    field_dirs: dict[str, Path],
    overall_dir: Path | None = None,
) -> int:
    """Draw every requested overlay for one page from already-extracted keys and page labels.

    The raw image is opened once; field_dirs maps field id -> output folder.
    """
    file_name = str(page.get("fileName") or "")
    image_path = config.Raw_Input / record_id / file_name
    if not image_path.is_file():
        logger.warning("no raw image for %s/%s", record_id, file_name)
        return 0
    font = ImageFont.load_default()
    image = Image.open(image_path).convert("RGBA")
    written = 0
    for field, out_dir in field_dirs.items():
        layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
        _draw_field(ImageDraw.Draw(layer), field, hits, labels, image.size, page_w, page_h, font)
        _save_page(image, layer, out_dir, record_id, page, file_name)
        written += 1
    if overall_dir is not None:
        layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
        _draw_overall(ImageDraw.Draw(layer), hits, labels, image.size, page_w, page_h, font)
        _save_page(image, layer, overall_dir, record_id, page, file_name)
        written += 1
    return written


_HEADING_COLORS = {"Heading": (190, 24, 93), "Subheading": (37, 99, 235)}
_HEADING_REJECTED = (120, 120, 120)


def _draw_headings(draw, rows, image_size, page_w: float, page_h: float, font) -> None:
    """Accepted headings boxed in their level colour; rejected near-misses thin and grey."""
    if not rows:
        draw.text((8, 8), "no heading boxes", fill=(220, 38, 38, 255), font=font)
        return
    for row in rows:
        box = _scale((row.box.left, row.box.top, row.box.right, row.box.bottom), image_size, page_w, page_h)
        color = _HEADING_COLORS.get(row.level, _COLORS[0]) if row.accepted else _HEADING_REJECTED
        draw.rectangle(box, outline=color + (255,), width=3 if row.accepted else 1)
        if row.accepted:
            draw.rectangle(box, fill=color + (30,))
        tag = "H" if row.level == "Heading" else "Sub"
        label = f"{tag} {row.score:.2f}" if row.accepted else f"{row.note} {row.score:.2f}"
        text_y = max(2, box[1] - 15)
        draw.rectangle((box[0], text_y, box[0] + 7 * len(label) + 6, text_y + 13), fill=(255, 255, 255, 210))
        draw.text((box[0] + 2, text_y), label, fill=color + (255,), font=font)


def render_heading_overlays(
    record_id: str,
    page: dict,
    headings: dict[str, list],
    page_w: float,
    page_h: float,
    dirs: dict[str, Path],
) -> int:
    """One overlay per heading detector (field -> folder) from already-extracted candidates."""
    file_name = str(page.get("fileName") or "")
    image_path = config.Raw_Input / record_id / file_name
    if not dirs or not image_path.is_file():
        return 0
    font = ImageFont.load_default()
    image = Image.open(image_path).convert("RGBA")
    written = 0
    for field, out_dir in dirs.items():
        layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
        _draw_headings(ImageDraw.Draw(layer), headings.get(field) or [], image.size, page_w, page_h, font)
        _save_page(image, layer, out_dir, record_id, page, file_name)
        written += 1
    return written

"""Draw trusted key boxes and their field overlay windows onto raw page images."""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from Util import config
from Util.geometry import KEYLESS_BAND_FRAC, boxes_overlap, page_size, words_from_page
from Util.keys import find_key_hits
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
}

_FIELD_ORDER = (
    "dob",
    "member_id",
    "name",
    "provider_name",
    "electronic_signature",
)


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
    from Member_Name.extract import ANCHOR_FIELDS, keyless_band

    name_boxes = [hit.value_box for hit in all_hits if hit.trusted and hit.field == "name" and hit.value_box]
    for anchor in all_hits:
        if not (anchor.trusted and anchor.field in ANCHOR_FIELDS):
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


def _save_page(image, layer, out_dir: Path, record_id: str, page: dict, file_name: str) -> Path:
    marked = Image.alpha_composite(image, layer).convert("RGB")
    page_number = int(page.get("pageNumber") or 0)
    record_dir = out_dir / record_id
    record_dir.mkdir(parents=True, exist_ok=True)
    dest = record_dir / f"page_{page_number:02d}_{file_name}"
    marked.save(dest)
    return dest


def save_overlays(documents, folder: Path, field: str = "dob", *, into_folder: bool = False) -> Path:
    """Write per-field overlays under ``folder/overlays`` (or directly into ``folder``)."""
    out_dir = folder if into_folder else folder / "overlays"
    out_dir.mkdir(parents=True, exist_ok=True)
    font = ImageFont.load_default()
    written = 0
    for document in documents:
        raw_dir = config.Raw_Input / document.record_id
        for page in document.pages:
            file_name = str(page.get("fileName") or "")
            image_path = raw_dir / file_name
            if not image_path.is_file():
                logger.warning("no raw image for %s/%s", document.record_id, file_name)
                continue
            words = words_from_page(page)
            page_w, page_h = page_size(page, words)
            all_hits = find_key_hits(words, page_w, page_h)
            hits = [hit for hit in all_hits if hit.trusted and hit.field == field]
            image = Image.open(image_path).convert("RGBA")
            layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(layer)
            color_index = 0
            if field == "name":
                color_index = _draw_keyless(draw, all_hits, image.size, page_w, page_h, font, color_index)
            for hit in hits:
                color = _FIELD_COLORS.get(field, _COLORS[color_index % len(_COLORS)])
                color_index += 1
                _draw_hit(draw, hit, image.size, page_w, page_h, color, font)
            if not hits and field != "name":
                label = {
                    "dob": "no DOB key",
                    "member_id": "no member ID key",
                    "name": "no name key",
                    "provider_name": "no provider key",
                    "electronic_signature": "no e-signature key",
                }.get(field, "no key")
                draw.text((8, 8), label, fill=(220, 38, 38, 255), font=font)
            dest = _save_page(image, layer, out_dir, document.record_id, page, file_name)
            written += 1
            logger.info("overlay %s/%s", document.record_id, dest.name)
    logger.info("wrote %s overlays in %s", written, out_dir)
    return out_dir


def save_overall_overlays(documents, folder: Path) -> Path:
    """One image per page with every trusted field overlay drawn together."""
    out_dir = folder
    out_dir.mkdir(parents=True, exist_ok=True)
    font = ImageFont.load_default()
    written = 0
    for document in documents:
        raw_dir = config.Raw_Input / document.record_id
        for page in document.pages:
            file_name = str(page.get("fileName") or "")
            image_path = raw_dir / file_name
            if not image_path.is_file():
                logger.warning("no raw image for %s/%s", document.record_id, file_name)
                continue
            words = words_from_page(page)
            page_w, page_h = page_size(page, words)
            all_hits = find_key_hits(words, page_w, page_h)
            image = Image.open(image_path).convert("RGBA")
            layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(layer)
            color_index = _draw_keyless(draw, all_hits, image.size, page_w, page_h, font, 0)
            any_hit = False
            for field in _FIELD_ORDER:
                color = _FIELD_COLORS.get(field, _COLORS[color_index % len(_COLORS)])
                for hit in all_hits:
                    if not (hit.trusted and hit.field == field):
                        continue
                    any_hit = True
                    _draw_hit(draw, hit, image.size, page_w, page_h, color, font)
                    color_index += 1
            if not any_hit:
                draw.text((8, 8), "no trusted keys", fill=(220, 38, 38, 255), font=font)
            dest = _save_page(image, layer, out_dir, document.record_id, page, file_name)
            written += 1
            logger.info("overall overlay %s/%s", document.record_id, dest.name)
    logger.info("wrote %s overall overlays in %s", written, out_dir)
    return out_dir

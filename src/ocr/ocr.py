"""OCR JPEG, JPG, and PNG pages with Docling (RapidOCR)."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import tempfile
import time
from pathlib import Path

# Hugging Face's cache uses symlinks; on Windows without Developer Mode that
# fails under parallel downloads. Copy files instead.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from src.config import Image_DPI, Image_Scale
from src.model_downloader.Docling_downloader import (
    DOCLING_MODELS_DIR,
    ensure_docling_models,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROCESSED = REPO_ROOT / "Data" / "Processed"

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png"}
SPLITTED_DIR_NAME = "splitted"
OCR_DIR_NAME = "ocr"
CONFIDENCE_CSV_NAME = "confidence.csv"

_CONVERTERS: dict[int, object] = {}


def _as_scale(scale: int | None) -> int:
    return int(Image_Scale if scale is None else scale)


def get_converter(scale: int | None = None):
    """Build a Docling converter using RapidOCR on images."""
    resolved = _as_scale(scale)
    cached = _CONVERTERS.get(resolved)
    if cached is not None:
        return cached

    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
    from docling.document_converter import DocumentConverter, ImageFormatOption

    ensure_docling_models()
    pipeline_options = PdfPipelineOptions(
        do_ocr=True,
        do_table_structure=True,
        artifacts_path=DOCLING_MODELS_DIR,
        ocr_options=RapidOcrOptions(
            backend="onnxruntime",
            lang=["english"],
            rapidocr_params={"Global.max_side_len": resolved},
        ),
    )
    converter = DocumentConverter(
        allowed_formats=[InputFormat.IMAGE],
        format_options={
            InputFormat.IMAGE: ImageFormatOption(pipeline_options=pipeline_options),
        },
    )
    _CONVERTERS[resolved] = converter
    return converter


def iter_page_images(splitted_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in splitted_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def iter_documents(processed_root: Path) -> list[Path]:
    documents: list[Path] = []
    if not processed_root.is_dir():
        return documents
    for doc_dir in sorted(processed_root.iterdir()):
        if not doc_dir.is_dir():
            continue
        splitted_dir = doc_dir / SPLITTED_DIR_NAME
        if splitted_dir.is_dir() and iter_page_images(splitted_dir):
            documents.append(doc_dir)
    return documents


def document_ocr_dir(doc_dir: Path) -> Path:
    return doc_dir / OCR_DIR_NAME


def clear_previous_ocr(ocr_dir: Path) -> None:
    if not ocr_dir.is_dir():
        return
    for leftover in ocr_dir.glob("page_*.*"):
        if leftover.suffix.lower() in {".txt", ".json"}:
            leftover.unlink()
    csv_path = ocr_dir / CONFIDENCE_CSV_NAME
    if csv_path.is_file():
        csv_path.unlink()


def _finite_float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number):
        return None
    return number


def collect_ocr_confidence(result) -> tuple[float | None, list[dict]]:
    """Read RapidOCR/Docling confidence from a conversion result."""
    page_confidence = None
    confidence = getattr(result, "confidence", None)
    if confidence is not None:
        page_confidence = _finite_float(getattr(confidence, "ocr_score", None))
        if page_confidence is None:
            pages = getattr(confidence, "pages", None) or {}
            scores = [
                score
                for score in (
                    _finite_float(getattr(page_scores, "ocr_score", None))
                    for page_scores in pages.values()
                )
                if score is not None
            ]
            if scores:
                page_confidence = sum(scores) / len(scores)

    lines: list[dict] = []
    for page in getattr(result, "pages", None) or []:
        parsed = getattr(page, "parsed_page", None)
        cells = getattr(parsed, "textline_cells", None) if parsed is not None else None
        if not cells:
            continue
        for cell in cells:
            text = (getattr(cell, "text", None) or getattr(cell, "orig", None) or "").strip()
            lines.append(
                {
                    "text": text,
                    "confidence": _finite_float(getattr(cell, "confidence", None)),
                }
            )
    return page_confidence, lines


def write_confidence_csv(ocr_dir: Path, pages: list[dict]) -> Path:
    path = ocr_dir / CONFIDENCE_CSV_NAME
    fieldnames = [
        "page",
        "source",
        "chars",
        "page_confidence",
        "line_index",
        "line_text",
        "line_confidence",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for number, page in enumerate(pages, start=1):
            source = Path(page["source"]).name
            chars = page.get("chars") or 0
            page_confidence = page.get("ocr_confidence")
            lines = page.get("lines") or []
            if not lines:
                writer.writerow(
                    {
                        "page": number,
                        "source": source,
                        "chars": chars,
                        "page_confidence": "" if page_confidence is None else f"{page_confidence:.6f}",
                        "line_index": "",
                        "line_text": "",
                        "line_confidence": "",
                    }
                )
                continue
            for line_index, line in enumerate(lines, start=1):
                line_confidence = line.get("confidence")
                writer.writerow(
                    {
                        "page": number,
                        "source": source,
                        "chars": chars,
                        "page_confidence": "" if page_confidence is None else f"{page_confidence:.6f}",
                        "line_index": line_index,
                        "line_text": line.get("text") or "",
                        "line_confidence": "" if line_confidence is None else f"{line_confidence:.6f}",
                    }
                )
    return path


def prepare_image_for_ocr(image_path: Path, max_side: int) -> Path:
    """Scale the full page so the longest side is <= max_side. Never crop."""
    from PIL import Image

    with Image.open(image_path) as img:
        rgb = img.convert("RGB")
        width, height = rgb.size
        longest = max(width, height)
        if longest <= max_side:
            return image_path

        ratio = max_side / longest
        resized = rgb.resize(
            (max(1, int(width * ratio)), max(1, int(height * ratio))),
            Image.Resampling.LANCZOS,
        )
        tmp_dir = Path(tempfile.mkdtemp(prefix="ner_ocr_"))
        tmp_path = tmp_dir / f"{image_path.stem}.jpg"
        resized.save(tmp_path, format="JPEG", quality=95, dpi=(Image_DPI, Image_DPI))
        return tmp_path


def ocr_image(
    image_path: Path,
    ocr_dir: Path,
    converter=None,
    scale: int | None = None,
) -> dict:
    """OCR one page and write ``<stem>.txt`` + ``<stem>.json`` under ocr_dir."""
    image_path = Path(image_path)
    ocr_dir = Path(ocr_dir)
    ocr_dir.mkdir(parents=True, exist_ok=True)
    resolved = _as_scale(scale)

    prepared = prepare_image_for_ocr(image_path, resolved)
    try:
        result = (converter or get_converter(resolved)).convert(str(prepared))
    finally:
        if prepared != image_path:
            prepared.unlink(missing_ok=True)
            try:
                prepared.parent.rmdir()
            except OSError:
                pass

    document = result.document
    text = document.export_to_text() or ""
    page_confidence, lines = collect_ocr_confidence(result)
    payload = {
        "source": image_path.name,
        "content": text,
        "model": "docling",
        "ocr_confidence": page_confidence,
        "document": document.export_to_dict(),
    }

    txt_path = ocr_dir / f"{image_path.stem}.txt"
    json_path = ocr_dir / f"{image_path.stem}.json"
    txt_path.write_text(text, encoding="utf-8")
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "source": image_path,
        "txt": txt_path,
        "json": json_path,
        "chars": len(text),
        "ocr_confidence": page_confidence,
        "lines": lines,
    }


def ocr_document(doc_dir: Path, converter=None, scale: int | None = None) -> list[dict]:
    """OCR every supported page in Processed/<document>/splitted/."""
    doc_dir = Path(doc_dir)
    splitted_dir = doc_dir / SPLITTED_DIR_NAME
    if not splitted_dir.is_dir():
        raise FileNotFoundError(f"Splitted folder not found: {splitted_dir}")

    pages = iter_page_images(splitted_dir)
    if not pages:
        raise RuntimeError(f"No JPEG/JPG/PNG pages found in {splitted_dir}")

    resolved = _as_scale(scale)
    ocr_dir = document_ocr_dir(doc_dir)
    ocr_dir.mkdir(parents=True, exist_ok=True)
    clear_previous_ocr(ocr_dir)

    if converter is None:
        load_start = time.perf_counter()
        converter = get_converter(resolved)
        print(f"  model load: {time.perf_counter() - load_start:.2f}s", flush=True)

    written: list[dict] = []
    for number, image_path in enumerate(pages, start=1):
        page_start = time.perf_counter()
        page = ocr_image(image_path, ocr_dir, converter=converter, scale=resolved)
        elapsed = time.perf_counter() - page_start
        page["seconds"] = elapsed
        written.append(page)
        print(
            f'  OCR Page: {number} File Name: "{image_path.name}"  {elapsed:.2f}s',
            flush=True,
        )
    csv_path = write_confidence_csv(ocr_dir, written)
    page_times = [page["seconds"] for page in written]
    avg = sum(page_times) / len(page_times)
    print(f"  confidence CSV -> {csv_path}", flush=True)
    print(f"  avg per page: {avg:.2f}s", flush=True)
    return written


def ocr_all(
    processed_root: Path = DEFAULT_PROCESSED,
    scale: int | None = None,
) -> dict[str, list[dict]]:
    """OCR every document that has a splitted folder under Processed."""
    processed_root = Path(processed_root)
    if not processed_root.is_dir():
        raise FileNotFoundError(f"Processed folder not found: {processed_root}")

    resolved = _as_scale(scale)
    documents = iter_documents(processed_root)
    print("Starting OCR", flush=True)
    print(f"Document Location: {processed_root}", flush=True)
    print(f"Total Documents: {len(documents)}", flush=True)
    print(f"Image Scale: {resolved}", flush=True)

    results: dict[str, list[dict]] = {}
    if not documents:
        print("No documents with JPEG/JPG/PNG pages found.", flush=True)
        return results

    load_start = time.perf_counter()
    converter = get_converter(resolved)
    print(f"model load: {time.perf_counter() - load_start:.2f}s", flush=True)
    for number, doc_dir in enumerate(documents, start=1):
        print(flush=True)
        print(f'OCR Document: {number} File Name: "{doc_dir.name}"', flush=True)
        pages = ocr_document(doc_dir, converter=converter, scale=resolved)
        results[doc_dir.name] = pages
        print(f"  {len(pages)} page(s) -> {document_ocr_dir(doc_dir)}", flush=True)
    all_times = [page["seconds"] for pages in results.values() for page in pages]
    if all_times:
        print(
            f"Overall avg per page: {sum(all_times) / len(all_times):.2f}s "
            f"({len(all_times)} page(s))",
            flush=True,
        )
    return results


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "OCR JPEG, JPG, and PNG pages from Data/Processed/<document>/splitted/ "
            "into Data/Processed/<document>/ocr/."
        )
    )
    parser.add_argument(
        "--processed",
        type=Path,
        default=DEFAULT_PROCESSED,
        help="Processed root folder (default: Data/Processed)",
    )
    parser.add_argument(
        "--scale",
        type=int,
        default=Image_Scale,
        help=f"RapidOCR max side length (default: {Image_Scale} from src/config.py)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        results = ocr_all(args.processed, scale=args.scale)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr, flush=True)
        return 1
    total_pages = sum(len(pages) for pages in results.values())
    print(f"Done. {len(results)} document(s), {total_pages} page(s).", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

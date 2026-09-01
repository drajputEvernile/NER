"""Single entry point: Raw documents -> split or ingest -> OCR -> person NER."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from src.config import Image_DPI, Image_Scale
from src.model_downloader._hf import ensure_all_ner_models
from src.model_downloader.Docling_downloader import ensure_docling_models
from src.NER.person_name.extract import extract_document
from src.ocr.ocr import get_converter, ocr_document
from src.splitter.split import (
    DEFAULT_INPUT,
    DEFAULT_OUTPUT,
    IMAGE_EXTENSIONS,
    SUPPORTED_EXTENSIONS,
    ingest_pre_split_folder,
    iter_image_pages,
    split_file,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def discover_raw_jobs(input_dir: Path) -> list[dict]:
    """Find PDF/TIF/TIFF files and folders of already-split PNG/JPG/JPEG pages."""
    jobs: list[dict] = []
    for item in sorted(input_dir.iterdir()):
        if item.is_file() and item.suffix.lower() in SUPPORTED_EXTENSIONS:
            jobs.append(
                {
                    "kind": "document",
                    "name": item.stem,
                    "path": item,
                }
            )
            continue
        if item.is_dir() and iter_image_pages(item):
            jobs.append(
                {
                    "kind": "pre_split",
                    "name": item.name,
                    "path": item,
                }
            )
    return jobs


def prepare_job(job: dict, output_root: Path, dpi: int) -> list[Path]:
    if job["kind"] == "document":
        return split_file(job["path"], output_root, dpi=dpi)
    return ingest_pre_split_folder(job["path"], output_root, dpi=dpi)


def run_pipeline(
    input_dir: Path = DEFAULT_INPUT,
    output_root: Path = DEFAULT_OUTPUT,
    dpi: int = Image_DPI,
    scale: int | None = Image_Scale,
) -> dict[str, list[dict]]:
    input_dir = Path(input_dir)
    output_root = Path(output_root)
    if not input_dir.is_dir():
        raise FileNotFoundError(f"Raw folder not found: {input_dir}")

    ensure_docling_models()
    ensure_all_ner_models()

    jobs = discover_raw_jobs(input_dir)
    print("Starting Pipeline", flush=True)
    print(f"Document Location: {input_dir}", flush=True)
    print(f"Total Documents: {len(jobs)}", flush=True)
    print(f"Max Image DPI: {dpi} (originals below this are kept)", flush=True)
    print(f"Image Scale: {scale}", flush=True)

    if not jobs:
        print("No PDF/TIF/TIFF files or pre-split PNG/JPG/JPEG folders found.", flush=True)
        return {}

    output_root.mkdir(parents=True, exist_ok=True)
    prepared: list[Path] = []
    for number, job in enumerate(jobs, start=1):
        print(flush=True)
        if job["kind"] == "document":
            print(f'Splitting Document: {number} File Name: "{job["path"].name}"', flush=True)
        else:
            print(
                f'Ingesting Splitted Document: {number} Folder Name: "{job["path"].name}"',
                flush=True,
            )
            print(f"  pages: {len(iter_image_pages(job['path']))} ({', '.join(sorted(IMAGE_EXTENSIONS))})", flush=True)
        pages = prepare_job(job, output_root, dpi=dpi)
        doc_dir = output_root / job["name"]
        prepared.append(doc_dir)
        print(f"  {len(pages)} page(s) -> {doc_dir / 'splitted'}", flush=True)

    print(flush=True)
    print("Starting OCR", flush=True)
    print(f"Total Documents: {len(prepared)}", flush=True)
    load_start = time.perf_counter()
    converter = get_converter(scale)
    print(f"model load: {time.perf_counter() - load_start:.2f}s", flush=True)
    results: dict[str, list[dict]] = {}
    for number, doc_dir in enumerate(prepared, start=1):
        print(flush=True)
        print(f'OCR Document: {number} File Name: "{doc_dir.name}"', flush=True)
        pages = ocr_document(doc_dir, converter=converter, scale=scale)
        results[doc_dir.name] = pages
        print(f"  {len(pages)} page(s) -> {doc_dir / 'ocr'}", flush=True)
    all_times = [page["seconds"] for pages in results.values() for page in pages]
    if all_times:
        print(
            f"Overall avg per page: {sum(all_times) / len(all_times):.2f}s "
            f"({len(all_times)} page(s))",
            flush=True,
        )

    print(flush=True)
    print("Starting NER", flush=True)
    print(f"Total Documents: {len(prepared)}", flush=True)
    for number, doc_dir in enumerate(prepared, start=1):
        print(flush=True)
        print(f'NER Document: {number} File Name: "{doc_dir.name}"', flush=True)
        extract_document(doc_dir)
    return results


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read Data/Raw, split PDF/TIF/TIFF or ingest already-split PNG/JPG/JPEG "
            "folders, OCR, then run every person-name NER model into "
            "Data/Processed/<document>/NER/<model_id>/."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help="Raw folder (default: Data/Raw)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Processed root folder (default: Data/Processed)",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=Image_DPI,
        help=f"Maximum image DPI (default: {Image_DPI})",
    )
    parser.add_argument(
        "--scale",
        type=int,
        default=Image_Scale,
        help=f"OCR max side length (default: {Image_Scale})",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        results = run_pipeline(args.input, args.output, dpi=args.dpi, scale=args.scale)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr, flush=True)
        return 1
    total_pages = sum(len(pages) for pages in results.values())
    print(f"Done. {len(results)} document(s), {total_pages} page(s).", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

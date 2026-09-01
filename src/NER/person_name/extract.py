"""Run every person-name NER checkpoint on saved OCR pages."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path

from src.NER.person_name.catalog import DEFAULT_PROCESSED, MODELS, model_by_id
from src.NER.person_name.locate import collect_text_boxes, format_polygon, locate_name
from src.NER.person_name.runners import run_model
from src.ocr.ocr import OCR_DIR_NAME

NER_DIR_NAME = "NER"
CSV_FIELDS = ["Page No", "Names", "Name Location"]


def _page_number(path: Path) -> int:
    match = re.search(r"(\d+)", path.stem)
    return int(match.group(1)) if match else 0


def iter_ocr_pages(ocr_dir: Path) -> list[tuple[int, Path, Path]]:
    pages: list[tuple[int, Path, Path]] = []
    if not ocr_dir.is_dir():
        return pages
    for json_path in sorted(ocr_dir.glob("page_*.json")):
        txt_path = json_path.with_suffix(".txt")
        pages.append((_page_number(json_path), json_path, txt_path))
    return pages


def iter_ner_documents(processed_root: Path) -> list[Path]:
    documents: list[Path] = []
    if not processed_root.is_dir():
        return documents
    for doc_dir in sorted(processed_root.iterdir()):
        if doc_dir.is_dir() and iter_ocr_pages(doc_dir / OCR_DIR_NAME):
            documents.append(doc_dir)
    return documents


def format_names_cell(names: list[str]) -> str:
    parts = []
    for name in names:
        cleaned = (name or "").replace('"', "'")
        parts.append(f'""{cleaned}""')
    return ", ".join(parts)


def format_locations_cell(locations: list[str]) -> str:
    parts = []
    for location in locations:
        cleaned = (location or "").replace('"', "'")
        parts.append(f'"{cleaned}"')
    return ", ".join(parts)


def page_text(json_path: Path, txt_path: Path) -> tuple[str, dict]:
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    if txt_path.is_file():
        text = txt_path.read_text(encoding="utf-8")
    else:
        text = payload.get("content") or ""
    return text, payload


def extract_page(spec: dict, json_path: Path, txt_path: Path) -> dict:
    text, payload = page_text(json_path, txt_path)
    hits = run_model(spec, text) if text.strip() else []
    boxes = collect_text_boxes(payload)
    names: list[str] = []
    locations: list[str] = []
    for hit in hits:
        name = hit.get("name") or ""
        if not name:
            continue
        polygon = locate_name(
            text,
            name,
            boxes,
            start=hit.get("start"),
            end=hit.get("end"),
        )
        names.append(name)
        locations.append(format_polygon(polygon) if polygon else "")
    return {"names": names, "locations": locations, "hits": hits}


def document_ner_dir(doc_dir: Path) -> Path:
    return doc_dir / NER_DIR_NAME


def model_output_dir(doc_dir: Path, spec: dict) -> Path:
    return document_ner_dir(doc_dir) / spec["id"]


def resolve_specs(model_id: str | None = None) -> list[dict]:
    if not model_id or model_id == "all":
        return list(MODELS)
    return [model_by_id(model_id)]


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        handle.write("Page No,Names,Name Location\r\n")
        for row in rows:
            handle.write(
                f"{row['Page No']},{row['Names']},{row['Name Location']}\r\n"
            )


def extract_document_model(doc_dir: Path, spec: dict) -> dict:
    """Run one NER model on a document and write its CSV under NER/<model_id>/."""
    doc_dir = Path(doc_dir)
    pages = iter_ocr_pages(doc_dir / OCR_DIR_NAME)
    if not pages:
        raise FileNotFoundError(f"No OCR JSON pages found in {doc_dir / OCR_DIR_NAME}")

    out_dir = model_output_dir(doc_dir, spec)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"{doc_dir.name}.csv"
    error_path = out_dir / "error.txt"
    if error_path.is_file():
        error_path.unlink()

    rows: list[dict] = []
    total_names = 0
    pages_with_names = 0
    for number, json_path, txt_path in pages:
        page_start = time.perf_counter()
        result = extract_page(spec, json_path, txt_path)
        elapsed = time.perf_counter() - page_start
        n_names = len(result["names"])
        total_names += n_names
        if n_names:
            pages_with_names += 1
        print(
            f'    NER Page: {number} File Name: "{json_path.name}"  '
            f"{n_names} name(s)  {elapsed:.2f}s",
            flush=True,
        )
        rows.append(
            {
                "Page No": number,
                "Names": format_names_cell(result["names"]),
                "Name Location": format_locations_cell(result["locations"]),
            }
        )

    _write_csv(csv_path, rows)
    return {
        "model": spec["id"],
        "ok": True,
        "csv": csv_path,
        "pages": len(pages),
        "pages_with_names": pages_with_names,
        "total_names": total_names,
        "error": "",
    }


def write_comparison(doc_dir: Path, summaries: list[dict]) -> Path:
    path = document_ner_dir(doc_dir) / "comparison.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "model",
                "ok",
                "pages",
                "pages_with_names",
                "total_names",
                "csv",
                "error",
            ],
        )
        writer.writeheader()
        for row in summaries:
            csv_path = row.get("csv")
            writer.writerow(
                {
                    "model": row.get("model") or "",
                    "ok": row.get("ok"),
                    "pages": row.get("pages") or 0,
                    "pages_with_names": row.get("pages_with_names") or 0,
                    "total_names": row.get("total_names") or 0,
                    "csv": str(csv_path) if csv_path else "",
                    "error": (row.get("error") or "")[:500],
                }
            )
    return path


def extract_document(doc_dir: Path, model_id: str | None = None) -> list[dict]:
    """Run every NER model (or one id) and write NER/<model_id>/<document>.csv."""
    doc_dir = Path(doc_dir)
    specs = resolve_specs(model_id)
    print(f"  NER models: {len(specs)}", flush=True)
    summaries: list[dict] = []
    for index, spec in enumerate(specs, start=1):
        print(flush=True)
        print(f'  NER Model: {index}/{len(specs)} "{spec["id"]}"', flush=True)
        try:
            load_start = time.perf_counter()
            run_model(spec, "warmup")
            print(f"    model load: {time.perf_counter() - load_start:.2f}s", flush=True)
            summaries.append(extract_document_model(doc_dir, spec))
        except Exception as exc:
            out_dir = model_output_dir(doc_dir, spec)
            out_dir.mkdir(parents=True, exist_ok=True)
            error = repr(exc)
            (out_dir / "error.txt").write_text(error, encoding="utf-8")
            print(f"    FAIL {spec['id']}: {error[:200]}", flush=True)
            summaries.append(
                {
                    "model": spec["id"],
                    "ok": False,
                    "csv": None,
                    "pages": 0,
                    "pages_with_names": 0,
                    "total_names": 0,
                    "error": error,
                }
            )
        else:
            print(f"    CSV -> {summaries[-1]['csv']}", flush=True)

    comparison = write_comparison(doc_dir, summaries)
    print(f"  comparison -> {comparison}", flush=True)
    return summaries


def extract_all(
    processed_root: Path = DEFAULT_PROCESSED,
    model_id: str | None = None,
) -> dict[str, list[dict]]:
    processed_root = Path(processed_root)
    documents = iter_ner_documents(processed_root)
    specs = resolve_specs(model_id)
    print("Starting NER", flush=True)
    print(f"Document Location: {processed_root}", flush=True)
    print(f"Total Documents: {len(documents)}", flush=True)
    print(f"NER models: {', '.join(spec['id'] for spec in specs)}", flush=True)

    results: dict[str, list[dict]] = {}
    if not documents:
        print("No documents with OCR pages found.", flush=True)
        return results

    for number, doc_dir in enumerate(documents, start=1):
        print(flush=True)
        print(f'NER Document: {number} File Name: "{doc_dir.name}"', flush=True)
        results[doc_dir.name] = extract_document(doc_dir, model_id=model_id)
    return results


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run every person-name NER model on Data/Processed/<document>/ocr/ "
            "and write Data/Processed/<document>/NER/<model_id>/<document>.csv."
        )
    )
    parser.add_argument(
        "--processed",
        type=Path,
        default=DEFAULT_PROCESSED,
        help="Processed root folder (default: Data/Processed)",
    )
    parser.add_argument(
        "--model",
        default="all",
        help="NER model id, or 'all' (default) to run every checkpoint",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        from src.model_downloader._hf import ensure_all_ner_models

        ensure_all_ner_models()
        results = extract_all(args.processed, model_id=args.model)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr, flush=True)
        return 1
    model_runs = sum(len(rows) for rows in results.values())
    print(
        f"Done. {len(results)} document(s), {model_runs} model output folder(s).",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

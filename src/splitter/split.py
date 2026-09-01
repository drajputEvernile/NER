"""Split TIF, TIFF, and PDF files from Data/Raw into JPEG pages."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageSequence

from src.config import Image_DPI

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = REPO_ROOT / "Data" / "Raw"
DEFAULT_OUTPUT = REPO_ROOT / "Data" / "Processed"

SUPPORTED_EXTENSIONS = {".pdf", ".tif", ".tiff"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
JPEG_QUALITY = 90


def to_rgb(image: Image.Image) -> Image.Image:
    """Convert any PIL mode into RGB suitable for JPEG."""
    if image.mode == "RGB":
        return image
    if image.mode in {"RGBA", "LA"}:
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        return background
    if image.mode == "P":
        return to_rgb(image.convert("RGBA"))
    return image.convert("RGB")


def source_dpi(image: Image.Image) -> float | None:
    """Read a page's tagged DPI, if it has a real one."""
    raw = image.info.get("dpi")
    if raw is None:
        return None
    if isinstance(raw, (tuple, list)):
        values = []
        for item in raw:
            try:
                number = float(item)
            except (TypeError, ValueError):
                continue
            if number > 1:
                values.append(number)
        return min(values) if values else None
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    return number if number > 1 else None


def fit_page_to_max_dpi(image: Image.Image, max_dpi: int) -> tuple[Image.Image, int]:
    """Keep DPI below max_dpi. Only shrink the full page if it is above max_dpi; never crop."""
    original = source_dpi(image)
    rgb = to_rgb(image)
    if original is None:
        return rgb, int(max_dpi)
    if original <= max_dpi:
        return rgb, int(round(original))

    ratio = max_dpi / original
    resized = rgb.resize(
        (max(1, int(rgb.width * ratio)), max(1, int(rgb.height * ratio))),
        Image.Resampling.LANCZOS,
    )
    return resized, int(max_dpi)


def save_jpeg(image: Image.Image, path: Path, dpi: int = Image_DPI) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    to_rgb(image).save(
        path,
        format="JPEG",
        quality=JPEG_QUALITY,
        optimize=True,
        dpi=(dpi, dpi),
    )


def iter_image_pages(folder: Path) -> list[Path]:
    return sorted(
        path
        for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    )


def ingest_pre_split_folder(
    folder: Path,
    output_root: Path = DEFAULT_OUTPUT,
    dpi: int = Image_DPI,
) -> list[Path]:
    """Copy already-split PNG/JPG/JPEG pages into Processed/<folder>/splitted/ with DPI rules."""
    folder = Path(folder)
    pages = iter_image_pages(folder)
    if not pages:
        raise RuntimeError(f"No PNG/JPG/JPEG pages found in {folder}")

    splitted_dir = output_root / folder.name / "splitted"
    splitted_dir.mkdir(parents=True, exist_ok=True)
    clear_previous_pages(splitted_dir)

    written: list[Path] = []
    used_stems: set[str] = set()
    for index, source in enumerate(pages, start=1):
        stem = source.stem
        if stem.lower() in used_stems:
            stem = f"page_{index:03d}"
        used_stems.add(stem.lower())
        out_path = splitted_dir / f"{stem}.jpg"
        with Image.open(source) as image:
            fitted, used_dpi = fit_page_to_max_dpi(image, dpi)
            save_jpeg(fitted, out_path, dpi=used_dpi)
        written.append(out_path)
    return written


def document_output_dir(output_root: Path, source: Path) -> Path:
    return output_root / source.stem / "splitted"


def clear_previous_pages(splitted_dir: Path) -> None:
    if not splitted_dir.is_dir():
        return
    for leftover in splitted_dir.glob("page_*.jpg"):
        leftover.unlink()


def split_tiff(source: Path, splitted_dir: Path, dpi: int = Image_DPI) -> list[Path]:
    written: list[Path] = []
    with Image.open(source) as document:
        for index, page in enumerate(ImageSequence.Iterator(document), start=1):
            fitted, used_dpi = fit_page_to_max_dpi(page, dpi)
            out_path = splitted_dir / f"page_{index:03d}.jpg"
            save_jpeg(fitted, out_path, dpi=used_dpi)
            written.append(out_path)
    return written


def split_pdf(source: Path, splitted_dir: Path, dpi: int = Image_DPI) -> list[Path]:
    import pypdfium2 as pdfium

    written: list[Path] = []
    pdf = pdfium.PdfDocument(str(source))
    try:
        scale = dpi / 72
        for index, page in enumerate(pdf, start=1):
            bitmap = page.render(scale=scale)
            image = bitmap.to_pil()
            out_path = splitted_dir / f"page_{index:03d}.jpg"
            save_jpeg(image, out_path, dpi=dpi)
            written.append(out_path)
            image.close()
    finally:
        pdf.close()
    return written


def split_file(
    source: Path,
    output_root: Path = DEFAULT_OUTPUT,
    dpi: int = Image_DPI,
) -> list[Path]:
    """Split one PDF/TIF/TIFF into JPEGs under Processed/<stem>/splitted/."""
    source = Path(source)
    suffix = source.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file type: {source.name}")

    splitted_dir = document_output_dir(output_root, source)
    splitted_dir.mkdir(parents=True, exist_ok=True)
    clear_previous_pages(splitted_dir)

    if suffix == ".pdf":
        pages = split_pdf(source, splitted_dir, dpi=dpi)
    else:
        pages = split_tiff(source, splitted_dir, dpi=dpi)

    if not pages:
        raise RuntimeError(f"No pages produced from {source.name}")
    return pages


def iter_raw_files(input_dir: Path) -> list[Path]:
    files = [
        path
        for path in sorted(input_dir.iterdir())
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    return files


def split_all(
    input_dir: Path = DEFAULT_INPUT,
    output_root: Path = DEFAULT_OUTPUT,
    dpi: int = Image_DPI,
) -> dict[str, list[Path]]:
    """Split every supported file in Raw into Processed/<document>/splitted/."""
    input_dir = Path(input_dir)
    output_root = Path(output_root)
    if not input_dir.is_dir():
        raise FileNotFoundError(f"Input folder not found: {input_dir}")

    output_root.mkdir(parents=True, exist_ok=True)
    results: dict[str, list[Path]] = {}
    files = iter_raw_files(input_dir)

    print("Starting Splitter")
    print(f"Document Location: {input_dir}")
    print(f"Total Documents: {len(files)}")
    print(f"Max Image DPI: {dpi} (originals below this are kept)")

    if not files:
        print("No PDF/TIF/TIFF files found.")
        return results

    for number, source in enumerate(files, start=1):
        print()
        print(f'Splitting Document: {number} File Name: "{source.name}"')
        pages = split_file(source, output_root, dpi=dpi)
        results[source.name] = pages
        print(f"  {len(pages)} page(s) -> {document_output_dir(output_root, source)}")
    return results


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Split TIF, TIFF, and PDF files from Data/Raw into JPEG pages "
            "under Data/Processed/<document>/splitted/."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help="Folder containing raw documents (default: Data/Raw)",
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
        help=f"Maximum output DPI (default: {Image_DPI} from src/config.py). Lower original DPI is kept.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        results = split_all(args.input, args.output, dpi=args.dpi)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    total_pages = sum(len(pages) for pages in results.values())
    print(f"Done. {len(results)} document(s), {total_pages} page(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

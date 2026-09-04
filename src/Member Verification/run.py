"""OCR Data/Raw records, then rule-based + GLiNER member verification.

Usage (from repo root):
  .\\"OLD NER"\\.venv\\Scripts\\python.exe "src\\Member Verification\\run.py"

Download local NER models first (Hugging Face is used only here):
  python "src\\Member Verification\\extractors\\ner_based\\ner_models\\model_downloader\\__main__.py"

Paths, OCR engines, and NER model toggles come from the repo-root .env
via each folder's config.py.
"""

from __future__ import annotations

import csv
import json
import logging
import sys
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
SRC = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(SRC))

import config as mv_config

sys.path.insert(0, str(HERE / "Rules"))
sys.path.insert(0, str(SRC / "OCR" / "Docling OCR"))
sys.path.insert(0, str(SRC / "OCR" / "Azure OCR"))

from extractors.ner_based.dob import extract_dob_ner
from extractors.ner_based.log import COLUMNS as NER_COLUMNS
from extractors.ner_based.log import reset as reset_ner_log
from extractors.ner_based.log import rows as ner_log_rows
from extractors.ner_based.member_id import extract_member_id_ner
from extractors.ner_based.model import unload as unload_ner_model
from extractors.ner_based.model import use_model
from extractors.ner_based.name import extract_name_ner
from extractors.ner_based.ner_models.catalog import by_id
from extractors.rule_based.dob import extract_dob
from extractors.rule_based.member_id import extract_member_id
from extractors.rule_based.name_2_words import extract_name_2_words
from extractors.rule_based.name_3_words import extract_name_3_words
from base_rules import is_present
from name_2_words_rules import verify_two_word_name
from name_3_words_rules import verify_three_word_name
from what_if_rules import apply_what_if
from docling_ocr import DoclingOcrExtractor
from azure_read_ocr import AzureReadOcrExtractor
import azure_read_ocr
import docling_ocr

SUPPORTED = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}

COLUMNS = [
    "RecordId",
    "Page_Count",
    "Page_No",
    "Detected_Full_Name",
    "Detected_DOB",
    "Detected_MemberID",
    "Page_Verified",
    "Document_Verified",
]


def detect_name_mode(fieldnames: list[str] | None) -> str:
    fields = {name.strip() for name in (fieldnames or []) if name}
    if {"DummyFirstName", "DummyMiddleName", "DummyLastName"} <= fields:
        return "3"
    if {"DummyFirstName", "DummyLastName"} <= fields:
        return "2"
    return ""


def load_system_input(path: Path) -> tuple[dict[str, dict[str, str]], str]:
    rows: dict[str, dict[str, str]] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        name_mode = detect_name_mode(reader.fieldnames)
        for row in reader:
            record_id = (row.get("RecordId") or "").strip()
            if not record_id:
                continue
            rows[record_id] = {
                "DummyFirstName": (row.get("DummyFirstName") or "").strip(),
                "DummyMiddleName": (row.get("DummyMiddleName") or "").strip(),
                "DummyLastName": (row.get("DummyLastName") or "").strip(),
                "DummyDOB": (row.get("DummyDOB") or "").strip(),
                "MemberID": (row.get("MemberID") or "").strip(),
            }
    return rows, name_mode


def list_record_pages(record_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in record_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED
    )


def list_raw_records(raw_dir: Path) -> list[Path]:
    if not raw_dir.is_dir():
        return []
    return sorted(path for path in raw_dir.iterdir() if path.is_dir())


def load_ocr_text(path: Path) -> str:
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("content"):
            return str(data["content"])
        txt = path.with_suffix(".txt")
        return txt.read_text(encoding="utf-8") if txt.is_file() else ""
    return path.read_text(encoding="utf-8")


def list_ocr_json(ocr_dir: Path) -> list[Path]:
    if not ocr_dir.is_dir():
        return []
    return sorted(path for path in ocr_dir.iterdir() if path.suffix.lower() == ".json")


def extract_full_name_rule(ocr_text: str, expected: dict[str, str], name_mode: str) -> str:
    if name_mode == "3":
        return extract_name_3_words(
            ocr_text,
            expected["DummyFirstName"],
            expected["DummyMiddleName"],
            expected["DummyLastName"],
        )
    if name_mode == "2":
        return extract_name_2_words(
            ocr_text,
            expected["DummyFirstName"],
            expected["DummyLastName"],
        )
    return "N/A"


def extract_page_fields(
    ocr_text: str,
    expected: dict[str, str],
    name_mode: str,
    model_id: str,
) -> tuple[str, str, str]:
    name = extract_full_name_rule(ocr_text, expected, name_mode)
    if not is_present(name):
        name = extract_name_ner(
            ocr_text,
            expected["DummyFirstName"],
            expected["DummyLastName"],
            expected["DummyMiddleName"],
            name_mode,
            model_id,
        )

    dob = extract_dob(ocr_text, expected["DummyDOB"])
    if not is_present(dob):
        dob = extract_dob_ner(ocr_text, expected["DummyDOB"], model_id)

    member_id = extract_member_id(ocr_text, expected["MemberID"])
    if not is_present(member_id):
        member_id = extract_member_id_ner(ocr_text, expected["MemberID"], model_id)

    return name, dob, member_id


def verify_page(expected: dict[str, str], name_mode: str, found_name: str, dob: str, member_id: str) -> bool:
    dob_ok = is_present(dob)
    id_ok = is_present(member_id)
    if name_mode == "3":
        status = verify_three_word_name(
            found_name,
            expected["DummyFirstName"],
            expected["DummyMiddleName"],
            expected["DummyLastName"],
            dob_ok,
            id_ok,
        )
    elif name_mode == "2":
        status = verify_two_word_name(
            found_name,
            expected["DummyFirstName"],
            expected["DummyLastName"],
            dob_ok,
            id_ok,
        )
    else:
        status = "Reject"
    return status == "Accept"


def document_verified(page_verified: list[bool], page_numbers: list[int], total: int) -> str:
    statuses = ["Accept" if ok else "Reject" for ok in page_verified]
    after = apply_what_if(statuses, page_numbers, total)
    if after and all(status == "Reject" for status in after):
        return "Reject"
    return "Accept"


def ocr_record(record_id: str, record_dir: Path) -> Path:
    pages = list_record_pages(record_dir)
    if not pages:
        raise SystemExit(f"No page images in {record_dir}")

    if docling_ocr.enabled:
        docling_dir = docling_ocr.record_output_dir(record_id)
        docling = DoclingOcrExtractor()
        for image_path in pages:
            logger.info("docling ocr %s / %s", record_id, image_path.name)
            docling.extract_page_outputs(image_path, docling_dir, cache_stem=image_path.stem)

    if azure_read_ocr.enabled:
        azure_dir = azure_read_ocr.record_output_dir(record_id)
        azure = AzureReadOcrExtractor()
        for image_path in pages:
            logger.info("azure ocr %s / %s", record_id, image_path.name)
            azure.extract_page_outputs(image_path, azure_dir, cache_stem=image_path.stem)

    ocr_read_dir = mv_config.record_ocr_read_dir(record_id)

    if not ocr_read_dir.is_dir():
        raise SystemExit(f"OCR output not found at {ocr_read_dir}")
    return ocr_read_dir


def verify_record(
    record_id: str,
    ocr_dir: Path,
    expected: dict[str, str],
    name_mode: str,
    model_id: str,
    member_dir: Path,
    ner_dir: Path,
) -> pd.DataFrame:
    use_model(model_id)
    reset_ner_log()
    pages = list_ocr_json(ocr_dir)
    total = len(pages)
    rows: list[dict[str, str | int | bool]] = []
    verified: list[bool] = []
    page_numbers: list[int] = []
    for page_no, ocr_path in enumerate(pages, start=1):
        text = load_ocr_text(ocr_path)
        name, dob, member_id = extract_page_fields(text, expected, name_mode, model_id)
        page_ok = verify_page(expected, name_mode, name, dob, member_id)
        verified.append(page_ok)
        page_numbers.append(page_no)
        rows.append(
            {
                "RecordId": record_id,
                "Page_Count": total,
                "Page_No": page_no,
                "Detected_Full_Name": name,
                "Detected_DOB": dob,
                "Detected_MemberID": member_id,
                "Page_Verified": page_ok,
            }
        )

    doc_status = document_verified(verified, page_numbers, total)
    for row in rows:
        row["Document_Verified"] = doc_status

    member_dir.mkdir(parents=True, exist_ok=True)
    ner_dir.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows, columns=COLUMNS)
    csv_path = member_dir / f"{model_id}.csv"
    frame.to_csv(csv_path, index=False)
    logger.info("wrote %s", csv_path)

    ner_frame = pd.DataFrame(ner_log_rows(), columns=NER_COLUMNS)
    ner_path = ner_dir / f"{model_id}.csv"
    ner_frame.to_csv(ner_path, index=False)
    logger.info("wrote %s", ner_path)
    unload_ner_model()
    return frame


def run() -> pd.DataFrame:
    system_path = mv_config.System_Input_path
    if not system_path.is_file():
        raise SystemExit(f"system_input.csv not found at {system_path}")
    system_rows, name_mode = load_system_input(system_path)
    if not system_rows:
        raise SystemExit(f"No records in {system_path}")

    enabled_ids = mv_config.enabled_model_ids()
    if not enabled_ids:
        raise SystemExit("No NER models enabled in config/.env")

    frames: list[pd.DataFrame] = []
    for record_dir in list_raw_records(mv_config.RAW_Read_Path):
        record_id = record_dir.name
        if record_id not in system_rows:
            logger.info("skip %s (not in system_input.csv)", record_id)
            continue
        ocr_dir = ocr_record(record_id, record_dir)
        member_dir = mv_config.record_mv_output_dir(record_id)
        ner_dir = mv_config.record_ner_output_dir(record_id)
        for model_id in enabled_ids:
            logger.info("ner model %s on %s", model_id, record_id)
            frame = verify_record(
                record_id,
                ocr_dir,
                system_rows[record_id],
                name_mode,
                by_id(model_id)["id"],
                member_dir,
                ner_dir,
            )
            frames.append(frame)

    if not frames:
        raise SystemExit(f"No matching records under {mv_config.RAW_Read_Path}")
    return pd.concat(frames, ignore_index=True)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    frame = run()
    print(frame.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

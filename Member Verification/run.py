"""Rule-based + GLiNER member verification.

OCR pages come from Azure Blob (AZURE_OCR_STORAGE_WRITE_PREFIX in .env).
CSV outputs go under absolute MV_OUTPUT_PATH / NER_OUTPUT_PATH from config.

Usage (from repo root):
  .\\.venv\\Scripts\\python.exe "Member Verification\\run.py"

Download NER models:
  .\\.venv\\Scripts\\python.exe Models\\model_downloader\\__main__.py
"""

from __future__ import annotations

import csv
import logging
import sys
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "azure_blob"))

import config as mv_config

sys.path.insert(0, str(HERE / "Rules"))

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
import azure_blob_storage as blob_store

COLUMNS = [
    "RecordId",
    "Total_Page_Count",
    "Page_No",
    "Detection_Source_Name",
    "ner_key_source_Name",
    "Detected_Full_Name",
    "Detection_Source_DOB",
    "ner_key_source_DOB",
    "Detected_DOB",
    "Detection_Source_MemberID",
    "ner_key_source_MemberID",
    "Detected_MemberID",
    "Page_Verified",
    "Document_Verified",
    "Page_Detection_Correct",
    "Page_Detection_InCorrect",
]


def page_text(page: dict) -> str:
    return str(page.get("content") or "")


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
) -> dict[str, str]:
    name = extract_full_name_rule(ocr_text, expected, name_mode)
    if is_present(name):
        name_source, name_key = "rule based", ""
    else:
        name, name_key = extract_name_ner(
            ocr_text,
            expected["DummyFirstName"],
            expected["DummyLastName"],
            expected["DummyMiddleName"],
            name_mode,
            model_id,
        )
        name_source = "ner" if is_present(name) else ""
        if not is_present(name):
            name_key = ""

    dob = extract_dob(ocr_text, expected["DummyDOB"])
    if is_present(dob):
        dob_source, dob_key = "rule based", ""
    else:
        dob, dob_key = extract_dob_ner(ocr_text, expected["DummyDOB"], model_id)
        dob_source = "ner" if is_present(dob) else ""
        if not is_present(dob):
            dob_key = ""

    member_id = extract_member_id(ocr_text, expected["MemberID"])
    if is_present(member_id):
        id_source, id_key = "rule based", ""
    else:
        member_id, id_key = extract_member_id_ner(ocr_text, expected["MemberID"], model_id)
        id_source = "ner" if is_present(member_id) else ""
        if not is_present(member_id):
            id_key = ""

    return {
        "Detected_Full_Name": name,
        "Detection_Source_Name": name_source,
        "ner_key_source_Name": name_key,
        "Detected_DOB": dob,
        "Detection_Source_DOB": dob_source,
        "ner_key_source_DOB": dob_key,
        "Detected_MemberID": member_id,
        "Detection_Source_MemberID": id_source,
        "ner_key_source_MemberID": id_key,
    }


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


def ocr_record(record_id: str) -> list[dict]:
    """Load OCR pages for a record from Azure Blob Storage."""
    if not blob_store.storage_configured():
        raise SystemExit("Azure Blob Storage is not configured in .env")
    try:
        blob_store.require_write_prefix()
    except RuntimeError as err:
        raise SystemExit(str(err)) from err
    pages = blob_store.load_ocr_pages(record_id)
    blob_name = blob_store.record_ocr_blob_name(record_id)
    if not pages:
        raise SystemExit(
            f"OCR JSON not found or empty in blob: "
            f"{blob_store.AZURE_STORAGE_CONTAINER}/{blob_name}"
        )
    pages = sorted(pages, key=lambda page: int(page.get("pageNumber") or 0))
    logger.info(
        "ocr source=blob record=%s pages=%s path=%s/%s",
        record_id,
        len(pages),
        blob_store.AZURE_STORAGE_CONTAINER,
        blob_name,
    )
    return pages


def verify_record(
    record_id: str,
    pages: list[dict],
    expected: dict[str, str],
    name_mode: str,
    model_id: str,
    member_dir: Path,
    ner_dir: Path,
) -> pd.DataFrame:
    use_model(model_id)
    reset_ner_log()
    total = len(pages)
    rows: list[dict[str, str | int | bool]] = []
    verified: list[bool] = []
    page_numbers: list[int] = []
    for index, page in enumerate(pages, start=1):
        text = page_text(page)
        page_no = int(page.get("pageNumber") or index)
        fields = extract_page_fields(text, expected, name_mode, model_id)
        page_ok = verify_page(
            expected,
            name_mode,
            fields["Detected_Full_Name"],
            fields["Detected_DOB"],
            fields["Detected_MemberID"],
        )
        verified.append(page_ok)
        page_numbers.append(page_no)
        rows.append(
            {
                "RecordId": record_id,
                "Total_Page_Count": total,
                "Page_No": page_no,
                **fields,
                "Page_Verified": page_ok,
                "Page_Detection_Correct": "",
                "Page_Detection_InCorrect": "",
            }
        )

    rows.sort(key=lambda row: int(row.get("Page_No") or 0))
    page_numbers = [int(row["Page_No"]) for row in rows]
    verified = [bool(row["Page_Verified"]) for row in rows]
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

    try:
        blob_store.require_write_prefix()
    except RuntimeError as err:
        raise SystemExit(str(err)) from err
    if not blob_store.storage_configured():
        raise SystemExit("Azure Blob Storage is not configured in .env")

    record_ids = blob_store.list_ocr_record_ids()
    record_source = f"{blob_store.AZURE_STORAGE_CONTAINER}/{blob_store.AZURE_OCR_STORAGE_WRITE_PREFIX}"
    logger.info("OCR read from blob: %s", record_source)
    logger.info("MV CSV root: %s", mv_config.MV_Output_path)
    logger.info("NER CSV root: %s", mv_config.NER_Output_path)

    frames: list[pd.DataFrame] = []
    for record_id in record_ids:
        if record_id not in system_rows:
            logger.info("skip %s (not in system_input.csv)", record_id)
            continue
        pages = ocr_record(record_id)
        member_dir = mv_config.record_mv_output_dir(record_id)
        ner_dir = mv_config.record_ner_output_dir(record_id)
        for model_id in enabled_ids:
            logger.info("ner model %s on %s", model_id, record_id)
            frame = verify_record(
                record_id,
                pages,
                system_rows[record_id],
                name_mode,
                by_id(model_id)["id"],
                member_dir,
                ner_dir,
            )
            frames.append(frame)

    if not frames:
        raise SystemExit(f"No matching records under {record_source}")
    return pd.concat(frames, ignore_index=True)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    frame = run()
    print(frame.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

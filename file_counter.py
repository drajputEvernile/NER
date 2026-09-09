"""Standalone Azure blob folder file counter.

Counts image files under each record folder at PREFIX and writes a CSV.

Auth: Microsoft Entra via browser login (cached). No az login required after
the first successful browser sign-in on this machine.

Usage:
  python file_counter.py
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

# ============================================================
# AZURE CONFIGURATION
# ============================================================
STORAGE_ACCOUNT = "azsadve2aipoc"
CONTAINER_NAME = "imaging-pipeline"
# Must match the folder that contains record folders.
PREFIX = "Raw_Input/Run1/Batch3/DEID_PNGs/"

# ============================================================
# FILE EXTENSIONS TO COUNT
# ============================================================
EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".jpe",
    ".jfif",
    ".png",
    ".bmp",
    ".dib",
    ".tif",
    ".tiff",
    ".webp",
    ".gif",
    ".ppm",
    ".pgm",
    ".pbm",
    ".pnm",
    ".jp2",
    ".j2k",
    ".pdf",
)

# ============================================================
# CSV OUTPUT (Desktop + copy next to this script)
# ============================================================
SCRIPT_DIR = Path(__file__).resolve().parent
CSV_NAME = "jpg_counts_batch3.csv"
CSV_DESKTOP = Path.home() / "Desktop" / CSV_NAME
CSV_LOCAL = SCRIPT_DIR / CSV_NAME


def log(msg: str = "") -> None:
    print(msg, flush=True)


def build_credential():
    """Browser login that works on a fresh PC without az login."""
    from azure.identity import (
        AzureCliCredential,
        ChainedTokenCredential,
        InteractiveBrowserCredential,
        TokenCachePersistenceOptions,
    )

    cache = TokenCachePersistenceOptions(
        name="ner_file_counter",
        allow_unencrypted_storage=True,
    )
    # Browser first so a machine without Azure CLI still opens a login window.
    # CLI is kept as a fast path when already logged in.
    return ChainedTokenCredential(
        InteractiveBrowserCredential(cache_persistence_options=cache),
        AzureCliCredential(process_timeout=30),
    )


def save_csv(rows: list[tuple[str, int]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    total = sum(count for _, count in rows)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Folder Name", "Count"])
        for folder_name, count in rows:
            writer.writerow([folder_name, count])
        writer.writerow(["Total", total])


def main() -> int:
    prefix = PREFIX if PREFIX.endswith("/") else f"{PREFIX}/"

    log("Connecting to Azure Blob Storage...")
    log(f"  account   : {STORAGE_ACCOUNT}")
    log(f"  container : {CONTAINER_NAME}")
    log(f"  prefix    : {prefix}")
    log("If a browser window opens, sign in with your work account.")
    log()

    try:
        from azure.storage.blob import BlobServiceClient

        credential = build_credential()
        blob_service = BlobServiceClient(
            account_url=f"https://{STORAGE_ACCOUNT}.blob.core.windows.net",
            credential=credential,
        )
        container = blob_service.get_container_client(CONTAINER_NAME)
        # Force auth now so login happens before the scan.
        next(container.list_blobs(name_starts_with=prefix), None)
        log("Azure connection OK.")
    except Exception as exc:
        log()
        log("ERROR connecting to Azure / signing in:")
        log(str(exc))
        log()
        log("Fix: allow the browser login, or run: az login")
        log("Then re-run this script.")
        input("\nPress Enter to close...")
        return 1

    log()
    log("=" * 70)
    log("Scanning Azure Blob")
    log("=" * 70)

    folder_counts: dict[str, int] = {}
    total_blobs_found = 0
    total_files_counted = 0
    sample_paths: list[str] = []

    try:
        for blob in container.list_blobs(name_starts_with=prefix):
            total_blobs_found += 1
            blob_name = blob.name or ""
            if len(sample_paths) < 20:
                sample_paths.append(blob_name)

            relative = blob_name[len(prefix) :] if blob_name.startswith(prefix) else blob_name
            relative = relative.strip("/")
            parts = relative.split("/") if relative else []
            if len(parts) < 2:
                continue

            folder_name = parts[0]
            filename = parts[-1]
            if not filename.lower().endswith(EXTENSIONS):
                continue

            folder_counts[folder_name] = folder_counts.get(folder_name, 0) + 1
            total_files_counted += 1
            if total_blobs_found % 5000 == 0:
                log(f"  ... scanned {total_blobs_found} blobs, counted {total_files_counted} files")
    except Exception as exc:
        log()
        log("ERROR while reading blobs:")
        log(str(exc))
        input("\nPress Enter to close...")
        return 1

    rows = sorted(folder_counts.items(), key=lambda item: item[0].casefold())
    total = sum(count for _, count in rows)

    log()
    log("=" * 70)
    log("SCAN RESULTS")
    log("=" * 70)
    log(f"Total blobs found under prefix : {total_blobs_found}")
    log(f"Total files counted            : {total_files_counted}")
    log(f"Total folders found            : {len(folder_counts)}")
    log()

    if not rows:
        log("NO FOLDERS / FILES WERE COUNTED.")
        log()
        log("Check CONTAINER_NAME and PREFIX.")
        log("Sample blob paths Azure returned:")
        if sample_paths:
            for path in sample_paths:
                log(f"  {path}")
        else:
            log("  (none — prefix may be wrong or empty)")
        log()
    else:
        log(f"{'Folder Name':<50} {'Count':>10}")
        log("-" * 50 + " " + "-" * 10)
        for folder_name, count in rows:
            log(f"{folder_name:<50} {count:>10}")
        log("-" * 50 + " " + "-" * 10)
        log(f"{'Total':<50} {total:>10}")

    # Always write CSV (even empty) so the script visibly produces an output file.
    saved: list[Path] = []
    for path in (CSV_DESKTOP, CSV_LOCAL):
        try:
            save_csv(rows, path)
            saved.append(path)
        except Exception as exc:
            log(f"ERROR saving CSV to {path}: {exc}")

    log()
    if saved:
        log("CSV saved to:")
        for path in saved:
            log(f"  {path}")
    else:
        log("ERROR: could not save CSV to Desktop or script folder.")

    log()
    input("Press Enter to close...")
    return 0 if saved else 1


if __name__ == "__main__":
    raise SystemExit(main())

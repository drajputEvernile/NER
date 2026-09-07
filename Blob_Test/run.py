"""Blob write smoke test.

Creates imaging-pipeline/OCR_Processed/ then uploads:
  Blob_Test/OCR_Processed/test1/IMG_0325.PNG
  -> imaging-pipeline/OCR_Processed/test1/IMG_0325.PNG

.env:
  AZURE_STORAGE_AUTH=entra
  AZURE_STORAGE_ACCOUNT_NAME=...
  AZURE_STORAGE_CONTAINER=imaging-pipeline
  AZURE_STORAGE_WRITE_PREFIX=OCR_Processed

Usage:
  python run.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import config as blob_config

logger = logging.getLogger(__name__)

_BLOB_SERVICE_CLIENT = None


def _entra_credential():
    from azure.identity import (
        AzureCliCredential,
        ChainedTokenCredential,
        InteractiveBrowserCredential,
        TokenCachePersistenceOptions,
    )

    cache = TokenCachePersistenceOptions(name="ner_blob_storage", allow_unencrypted_storage=True)
    return ChainedTokenCredential(
        AzureCliCredential(process_timeout=30),
        InteractiveBrowserCredential(cache_persistence_options=cache),
    )


def blob_service_client():
    global _BLOB_SERVICE_CLIENT
    if _BLOB_SERVICE_CLIENT is not None:
        return _BLOB_SERVICE_CLIENT

    from azure.storage.blob import BlobServiceClient

    if blob_config.use_entra():
        account = blob_config.AZURE_STORAGE_ACCOUNT_NAME
        if not account:
            raise SystemExit("AZURE_STORAGE_ACCOUNT_NAME is required for Entra auth")
        logger.info("auth=microsoft_entra account=%s", account)
        _BLOB_SERVICE_CLIENT = BlobServiceClient(
            account_url=f"https://{account}.blob.core.windows.net",
            credential=_entra_credential(),
        )
        return _BLOB_SERVICE_CLIENT

    if blob_config.AZURE_STORAGE_CONNECTION_STRING:
        logger.info("auth=connection_string")
        _BLOB_SERVICE_CLIENT = BlobServiceClient.from_connection_string(
            blob_config.AZURE_STORAGE_CONNECTION_STRING
        )
        return _BLOB_SERVICE_CLIENT

    account = blob_config.AZURE_STORAGE_ACCOUNT_NAME
    key = blob_config.AZURE_STORAGE_ACCOUNT_KEY
    logger.info("auth=account_key account=%s", account)
    _BLOB_SERVICE_CLIENT = BlobServiceClient(
        account_url=f"https://{account}.blob.core.windows.net",
        credential=key,
    )
    return _BLOB_SERVICE_CLIENT


def container_client():
    if not blob_config.storage_configured():
        raise SystemExit(
            "Azure Blob Storage is not configured. Set ACCOUNT_NAME + CONTAINER in .env"
        )
    return blob_service_client().get_container_client(blob_config.AZURE_STORAGE_CONTAINER)


def write_prefix() -> str:
    value = blob_config.AZURE_STORAGE_WRITE_PREFIX
    return f"{value}/" if value else ""


def local_upload_files() -> list[Path]:
    root = blob_config.LOCAL_Upload_Path
    if not root.is_dir():
        raise SystemExit(f"Local upload folder not found: {root}")
    files = sorted(path for path in root.rglob("*") if path.is_file())
    if not files:
        raise SystemExit(f"No files under {root}")
    return files


def test_write() -> None:
    client = container_client()
    dest_root = write_prefix()
    if not dest_root:
        raise SystemExit("AZURE_STORAGE_WRITE_PREFIX is empty")

    files = local_upload_files()
    logger.info(
        "WRITE test: upload %s file(s) from %s -> %s/%s",
        len(files),
        blob_config.LOCAL_Upload_Path,
        blob_config.AZURE_STORAGE_CONTAINER,
        dest_root.rstrip("/"),
    )

    # Blob "folders" are virtual. Uploading OCR_Processed/test1/file.png
    # creates OCR_Processed and test1 in the portal — no empty marker blobs.
    uploaded: list[str] = []
    for path in files:
        relative = path.relative_to(blob_config.LOCAL_Upload_Path).as_posix()
        blob_name = f"{dest_root}{relative}"
        logger.info("upload %s -> %s", path, blob_name)
        with path.open("rb") as handle:
            client.upload_blob(name=blob_name, data=handle, overwrite=True)
        uploaded.append(blob_name)

    for blob_name in uploaded:
        props = client.get_blob_client(blob_name).get_blob_properties()
        logger.info("WRITE verified: %s (%s bytes)", blob_name, props.size)

    logger.info("WRITE ok: %s file(s) under %s", len(uploaded), dest_root.rstrip("/"))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("azure").setLevel(logging.WARNING)
    logging.getLogger("azure.identity").setLevel(logging.WARNING)

    logger.info("container=%s", blob_config.AZURE_STORAGE_CONTAINER)
    logger.info("write_prefix=%s", blob_config.AZURE_STORAGE_WRITE_PREFIX or "(root)")
    logger.info("auth_mode=%s", blob_config.AZURE_STORAGE_AUTH)

    test_write()
    logger.info("done: write test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

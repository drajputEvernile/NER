"""List the first folder under the blob PREFIX and copy it locally.

This storage account requires Microsoft Entra auth (account keys are blocked).

.env:
  AZURE_STORAGE_AUTH=entra
  AZURE_STORAGE_ACCOUNT_NAME=...
  AZURE_STORAGE_CONTAINER=imaging-pipeline
  AZURE_STORAGE_PREFIX=Raw_Input/Run1/Batch1/Deid_Images

Then sign in once:
  az login

Local copy goes to:
  Blob_Test/output/{first_folder_name}/...

Usage (from repo root):
  .\\.venv\\Scripts\\python.exe Blob_Test\\run.py
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


def blob_service_client():
    from azure.storage.blob import BlobServiceClient

    if blob_config.use_entra():
        from azure.identity import DefaultAzureCredential

        account = blob_config.AZURE_STORAGE_ACCOUNT_NAME
        if not account:
            raise SystemExit("AZURE_STORAGE_ACCOUNT_NAME is required for Entra auth")
        logger.info("auth=microsoft_entra account=%s", account)
        return BlobServiceClient(
            account_url=f"https://{account}.blob.core.windows.net",
            credential=DefaultAzureCredential(exclude_interactive_browser_credential=False),
        )

    if blob_config.AZURE_STORAGE_CONNECTION_STRING:
        logger.info("auth=connection_string")
        return BlobServiceClient.from_connection_string(blob_config.AZURE_STORAGE_CONNECTION_STRING)

    account = blob_config.AZURE_STORAGE_ACCOUNT_NAME
    key = blob_config.AZURE_STORAGE_ACCOUNT_KEY
    logger.info("auth=account_key account=%s", account)
    return BlobServiceClient(account_url=f"https://{account}.blob.core.windows.net", credential=key)


def container_client():
    if not blob_config.storage_configured():
        if blob_config.use_entra():
            raise SystemExit(
                "Azure Blob Storage is not configured for Entra. Set AZURE_STORAGE_ACCOUNT_NAME "
                "and AZURE_STORAGE_CONTAINER in .env, then run: az login"
            )
        raise SystemExit(
            "Azure Blob Storage is not configured. Set AZURE_STORAGE_CONTAINER and either "
            "AZURE_STORAGE_CONNECTION_STRING or AZURE_STORAGE_ACCOUNT_NAME + AZURE_STORAGE_ACCOUNT_KEY in .env"
        )
    return blob_service_client().get_container_client(blob_config.AZURE_STORAGE_CONTAINER)


def prefix() -> str:
    value = blob_config.AZURE_STORAGE_PREFIX
    return f"{value}/" if value else ""


def list_folders() -> list[str]:
    client = container_client()
    base = prefix()
    names: list[str] = []
    for item in client.walk_blobs(name_starts_with=base, delimiter="/"):
        name = getattr(item, "name", "") or ""
        if not name.endswith("/"):
            continue
        relative = name[len(base) :] if base and name.startswith(base) else name
        folder = relative.strip("/")
        if folder and "/" not in folder:
            names.append(folder)
    return sorted(set(names))


def list_blobs_in_folder(folder_name: str) -> list[str]:
    client = container_client()
    base = f"{prefix()}{folder_name}/"
    blobs: list[str] = []
    for blob in client.list_blobs(name_starts_with=base):
        name = blob.name
        if name.endswith("/"):
            continue
        blobs.append(name)
    return sorted(blobs)


def download_folder(folder_name: str, dest_root: Path) -> Path:
    dest = dest_root / folder_name
    dest.mkdir(parents=True, exist_ok=True)
    client = container_client()
    blobs = list_blobs_in_folder(folder_name)
    if not blobs:
        logger.info("folder %s has no files", folder_name)
        return dest

    logger.info("downloading %s file(s) from %s", len(blobs), folder_name)
    for blob_name in blobs:
        relative = blob_name
        base = f"{prefix()}{folder_name}/"
        if relative.startswith(base):
            relative = relative[len(base) :]
        target = dest / Path(*relative.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file() and target.stat().st_size > 0:
            logger.info("skip (exists) %s", target)
            continue
        logger.info("download %s -> %s", blob_name, target)
        with target.open("wb") as handle:
            client.download_blob(blob_name).readinto(handle)
    return dest


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("azure").setLevel(logging.WARNING)
    logging.getLogger("azure.identity").setLevel(logging.INFO)

    logger.info("container=%s", blob_config.AZURE_STORAGE_CONTAINER)
    logger.info("prefix=%s", blob_config.AZURE_STORAGE_PREFIX or "(root)")
    logger.info("auth_mode=%s", blob_config.AZURE_STORAGE_AUTH)

    folders = list_folders()
    logger.info("found %s folder(s) under prefix", len(folders))
    if not folders:
        raise SystemExit("No folders found under the configured PREFIX")

    first = folders[0]
    logger.info("first folder=%s", first)
    for index, name in enumerate(folders[:10], start=1):
        logger.info("  %s. %s", index, name)
    if len(folders) > 10:
        logger.info("  ... and %s more", len(folders) - 10)

    dest = download_folder(first, blob_config.LOCAL_Download_Path)
    files = [path for path in dest.rglob("*") if path.is_file()]
    logger.info("done. copied folder to %s (%s file(s))", dest, len(files))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

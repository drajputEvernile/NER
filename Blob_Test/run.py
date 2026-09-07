"""List the first folder under the blob PREFIX and copy it locally.

This storage account requires Microsoft Entra auth (account keys are blocked).

.env:
  AZURE_STORAGE_AUTH=entra
  AZURE_STORAGE_ACCOUNT_NAME=...
  AZURE_STORAGE_CONTAINER=imaging-pipeline
  AZURE_STORAGE_PREFIX=Raw_Input/Run1/Batch1/Deid_Images

Then sign in once (recommended):
  az login

After that, runs reuse the CLI token. If az is not logged in, the browser opens once
and the token is cached for later runs.

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

_BLOB_SERVICE_CLIENT = None


def _entra_credential():
    """Login once via az login, or once via browser with a persistent token cache."""
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
        logger.info("auth=microsoft_entra account=%s (cli cache or one browser login)", account)
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


def _item_name(item) -> str:
    return str(getattr(item, "name", "") or "")


def _is_prefix(item) -> bool:
    name = _item_name(item)
    return name.endswith("/") or type(item).__name__ == "BlobPrefix"


def list_children(base: str) -> tuple[list[str], list[str]]:
    """Return (folder_names, file_names) directly under base using delimiter listing."""
    client = container_client()
    folders: list[str] = []
    files: list[str] = []
    for item in client.walk_blobs(name_starts_with=base, delimiter="/"):
        name = _item_name(item)
        if not name:
            continue
        if _is_prefix(item) or name.endswith("/"):
            relative = name[len(base) :] if base and name.startswith(base) else name
            folder = relative.strip("/")
            if folder and "/" not in folder:
                folders.append(folder)
            continue
        relative = name[len(base) :] if base and name.startswith(base) else name
        if relative and "/" not in relative.strip("/"):
            files.append(Path(name).name)
    return sorted(set(folders)), sorted(set(files))


def list_folders_from_blobs(base: str) -> list[str]:
    """Fallback: derive immediate child folders from flat blob names under base."""
    client = container_client()
    names: set[str] = set()
    count = 0
    for blob in client.list_blobs(name_starts_with=base):
        count += 1
        name = blob.name
        if base and not name.startswith(base):
            continue
        relative = name[len(base) :] if base else name
        relative = relative.strip("/")
        if not relative:
            continue
        folder = relative.split("/", 1)[0]
        if folder:
            names.add(folder)
        if count >= 5000:
            break
    logger.info("fallback scanned %s blob(s) under %r", count, base or "(root)")
    return sorted(names)


def probe_path() -> None:
    """Print what exists at container root and each PREFIX segment."""
    client = container_client()
    segments = [part for part in blob_config.AZURE_STORAGE_PREFIX.split("/") if part]
    current = ""
    logger.info("probing container path...")
    folders, files = list_children(current)
    logger.info("  / -> folders=%s files=%s", folders[:20], files[:10])

    built: list[str] = []
    for part in segments:
        built.append(part)
        current = "/".join(built) + "/"
        folders, files = list_children(current)
        logger.info("  /%s -> folders=%s files=%s", "/".join(built), folders[:20], files[:10])
        if not folders and not files:
            # show a few raw blob names that start with a shorter prefix for clues
            samples = []
            for blob in client.list_blobs(name_starts_with="/".join(built[: max(1, len(built) - 1)]) + "/"):
                samples.append(blob.name)
                if len(samples) >= 5:
                    break
            if samples:
                logger.info("  sample blobs near here: %s", samples)
            break


def list_folders() -> list[str]:
    base = prefix()
    folders, files = list_children(base)
    if files and not folders:
        logger.info("prefix has %s loose file(s), no subfolders", len(files))
    if folders:
        return folders
    # walk_blobs sometimes returns nothing for virtual dirs; derive from blob names
    return list_folders_from_blobs(base)


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
    logging.getLogger("azure.identity").setLevel(logging.WARNING)

    logger.info("container=%s", blob_config.AZURE_STORAGE_CONTAINER)
    logger.info("prefix=%s", blob_config.AZURE_STORAGE_PREFIX or "(root)")
    logger.info("auth_mode=%s", blob_config.AZURE_STORAGE_AUTH)

    probe_path()
    folders = list_folders()
    logger.info("found %s folder(s) under prefix", len(folders))
    if not folders:
        raise SystemExit(
            "No folders found under the configured PREFIX. "
            "Check the probe output above and fix AZURE_STORAGE_PREFIX casing/path in .env"
        )

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

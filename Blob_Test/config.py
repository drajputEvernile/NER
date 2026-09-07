"""Blob_Test settings. Credentials come from the repo-root .env."""

from __future__ import annotations

import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent

# Local folder to upload for the write test:
#   Blob_Test/OCR_Processed/test1/IMG_0325.PNG
LOCAL_Upload_Path = HERE / "OCR_Processed"

# Blob destination under the container (sibling of Raw_Input):
#   imaging-pipeline/OCR_Processed/test1/...
AZURE_STORAGE_WRITE_PREFIX = "OCR_Processed"


def _load_repo_env() -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_repo_env()

# entra = Microsoft Entra ID (required when account keys are disabled)
# key   = connection string or account name + key
AZURE_STORAGE_AUTH = (os.environ.get("AZURE_STORAGE_AUTH") or "entra").strip().casefold()

AZURE_STORAGE_CONNECTION_STRING = (os.environ.get("AZURE_STORAGE_CONNECTION_STRING") or "").strip()
AZURE_STORAGE_ACCOUNT_NAME = (os.environ.get("AZURE_STORAGE_ACCOUNT_NAME") or "").strip()
AZURE_STORAGE_ACCOUNT_KEY = (os.environ.get("AZURE_STORAGE_ACCOUNT_KEY") or "").strip()
AZURE_STORAGE_CONTAINER = (os.environ.get("AZURE_STORAGE_CONTAINER") or "").strip()
AZURE_STORAGE_PREFIX = (os.environ.get("AZURE_STORAGE_PREFIX") or "").strip().strip("/")
AZURE_STORAGE_WRITE_PREFIX = (
    os.environ.get("AZURE_STORAGE_WRITE_PREFIX") or AZURE_STORAGE_WRITE_PREFIX
).strip().strip("/")


def storage_configured() -> bool:
    if not AZURE_STORAGE_CONTAINER:
        return False
    if AZURE_STORAGE_AUTH in {"entra", "aad", "azuread"}:
        return bool(AZURE_STORAGE_ACCOUNT_NAME)
    if AZURE_STORAGE_CONNECTION_STRING:
        return True
    return bool(AZURE_STORAGE_ACCOUNT_NAME and AZURE_STORAGE_ACCOUNT_KEY)


def use_entra() -> bool:
    return AZURE_STORAGE_AUTH in {"entra", "aad", "azuread"}

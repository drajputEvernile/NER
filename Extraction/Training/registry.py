"""Extraction model versions a run can use.

v0 is the hand-written rules. A trained version vNNN is one or two folders in
{config.Extraction_Models} (written by Training/train.py): KV_vNNN for the KV_Extraction model and
Heading_vNNN for the Heading_Detector model. The rules still find every candidate; a version's
models pick among them for the fields they trained, and every other field keeps the rules' choice.
That folder is committed to git, so a trained version travels with the repo.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from Util import config

RULES_VERSION = "v0"
_FOLDER = re.compile(r"(KV|Heading)_v(\d{3})")
_TITLES = {"KV": "KV Extraction", "Heading": "Heading Detector"}


def _manifest(folder: Path) -> dict[str, Any]:
    try:
        return json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _folders(version: str) -> dict[str, Path]:
    """prefix (KV / Heading) -> the version's folder, for each model that has it."""
    root = Path(config.Extraction_Models)
    return {prefix: root / f"{prefix}_{version}" for prefix in _TITLES if (root / f"{prefix}_{version}").is_dir()}


def _runnable_folder(folder: Path) -> bool:
    return bool(_manifest(folder).get("fields_trained")) and (folder / "thresholds.json").is_file()


def _version_names() -> list[str]:
    root = Path(config.Extraction_Models)
    if not root.is_dir():
        return []
    return sorted({f"v{m.group(2)}" for path in root.iterdir() if path.is_dir() and (m := _FOLDER.fullmatch(path.name))})


def list_versions() -> list[dict[str, Any]]:
    versions: list[dict[str, Any]] = [
        {
            "id": RULES_VERSION,
            "label": "v0 · Rules",
            "description": "Hand-written key and location rules pick every value.",
            "trained_at": None,
            "runnable": True,
        }
    ]
    for name in _version_names():
        found = _folders(name)
        manifests = {prefix: _manifest(folder) for prefix, folder in found.items()}
        models = " + ".join(_TITLES[prefix] for prefix in found)
        trained = [m.get("trained_at") for m in manifests.values() if m.get("trained_at")]
        versions.append(
            {
                "id": name,
                "label": f"{name} · {models}",
                "description": "; ".join(str(m.get("description") or "") for m in manifests.values() if m.get("description"))
                or "Rules find candidates; trained models pick.",
                "trained_at": max(trained) if trained else None,
                "runnable": is_runnable(name),
            }
        )
    return versions


def is_runnable(version: str) -> bool:
    if version == RULES_VERSION:
        return True
    return any(_runnable_folder(folder) for folder in _folders(version).values())


def next_version() -> str:
    """The next free number across the trained models and the retired ones, so a number is never reused."""
    numbers = [int(name[1:]) for name in _version_names()]
    retired = Path(config.Retired_Models)
    if retired.is_dir():
        numbers += [int(m.group(1)) for path in retired.rglob("v[0-9][0-9][0-9]") if path.is_dir() and (m := re.fullmatch(r"v(\d{3})", path.name))]
    return f"v{max(numbers, default=0) + 1:03d}"

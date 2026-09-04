"""Load the repo-root .env for secrets and toggles. Data paths live in each folder's config.py."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

_SRC = Path(__file__).resolve().parent
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}
_ENV_LOADED = False


def repo_root() -> Path:
    for candidate in [_SRC.parent, _SRC, *_SRC.parent.parents]:
        if (candidate / ".env").is_file():
            return candidate
    return _SRC.parent


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_env() -> dict[str, str]:
    global _ENV_LOADED
    values = _parse_env_file(repo_root() / ".env")
    if not _ENV_LOADED:
        for key, value in values.items():
            os.environ.setdefault(key, value)
        _ENV_LOADED = True
    return values


def _get(key: str, default: str = "") -> str:
    load_env()
    value = os.environ.get(key)
    if value is None or not str(value).strip():
        return default
    return str(value).strip()


def env_str(key: str, default: str = "") -> str:
    return _get(key, default)


def env_bool(key: str, default: bool = False) -> bool:
    raw = _get(key, "true" if default else "false").casefold()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    return default


def env_int(key: str, default: int = 0) -> int:
    raw = _get(key, str(default))
    try:
        return int(raw)
    except ValueError:
        return default


def repo_path(*parts: str) -> Path:
    path = Path(*parts) if parts else Path()
    if path.is_absolute():
        return path
    return (repo_root() / path).resolve()


def load_sibling_config(caller_file: str | Path, name: str) -> ModuleType:
    path = Path(caller_file).resolve().parent / "config.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

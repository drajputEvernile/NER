"""A run's tables: one CSV file per sheet in {run}/KV_Extraction/ (Training/features.py has the layout).

A sheet is written on its own, so saving a review rewrites only the sheet it changed. Every file is
written atomically (a temp file replaces it), so a stopped run or a crash never leaves half a file. If
another program holds a file open (an editor, a scanner) the replace fails with an OSError
(PermissionError) and the caller keeps its rows and tries again.

Files are UTF-8 with a byte-order mark, so spreadsheet programs open them with the right characters;
everything is read back as text ("" for an empty cell).
"""

from __future__ import annotations

import itertools
import os
from pathlib import Path
from typing import Iterable

import pandas as pd

ENCODING = "utf-8-sig"
_counter = itertools.count()


def table_path(folder: Path, name: str) -> Path:
    return Path(folder) / f"{name}.csv"


def write_table(folder: Path, name: str, frame: pd.DataFrame) -> Path:
    """Write one sheet (header = the frame's columns), replacing its CSV."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = table_path(folder, name)
    # A temp name no other write shares, so two writes can never collide on it.
    temp = folder / f"{name}.csv.{os.getpid()}.{next(_counter)}.tmp"
    try:
        frame.to_csv(temp, index=False, encoding=ENCODING, lineterminator="\n")
        os.replace(temp, path)
    except OSError:
        temp.unlink(missing_ok=True)
        raise
    return path


def write_tables(folder: Path, frames: dict[str, pd.DataFrame], only: Iterable[str] | None = None) -> Path:
    """Write the named sheets (all of them by default)."""
    for name in only if only is not None else frames:
        write_table(folder, name, frames[name])
    return Path(folder)


def read_table(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False, encoding=ENCODING)


def read_tables(folder: Path, names: Iterable[str] | None = None) -> dict[str, pd.DataFrame]:
    """The sheets that exist in the folder (or just the named ones), as text."""
    folder = Path(folder)
    if names is None:
        names = sorted(path.stem for path in folder.glob("*.csv"))
    return {name: read_table(table_path(folder, name)) for name in names if table_path(folder, name).is_file()}


def tables_signature(folder: Path, names: Iterable[str]) -> tuple:
    """(size, modified time) of each sheet's file: changes whenever any of them is rewritten."""
    out = []
    for name in names:
        try:
            stat = table_path(folder, name).stat()
            out.append((stat.st_size, stat.st_mtime_ns))
        except OSError:
            out.append(None)
    return tuple(out)

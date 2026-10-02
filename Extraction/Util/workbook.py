"""Read and write a run's workbook: one .xlsx, one sheet per table (see Training/features.py).

Writes are atomic (a temp file replaces the workbook), so a stopped run or a crash never leaves
half a file. If the workbook is open in Excel the replace fails with an OSError (PermissionError):
the caller keeps its rows and tries again.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font
from openpyxl.utils.exceptions import IllegalCharacterError  # noqa: F401 - documents what _clean avoids
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

_HEADER_FONT = Font(name="Arial", bold=True, size=11)


def _clean(value):
    if value is None:
        return None
    if isinstance(value, float) and value != value:  # NaN
        return None
    if isinstance(value, str):
        return ILLEGAL_CHARACTERS_RE.sub("", value)
    return value


def write_workbook(path: Path, sheets: dict[str, pd.DataFrame]) -> Path:
    """Write every sheet (name -> frame, header = the frame's columns) to `path`, replacing it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    book = Workbook(write_only=True)
    for title, frame in sheets.items():
        sheet = book.create_sheet(title)
        sheet.freeze_panes = "A2"
        header = []
        for name in frame.columns:
            cell = WriteOnlyCell(sheet, value=str(name))
            cell.font = _HEADER_FONT
            header.append(cell)
        sheet.append(header)
        for row in frame.astype(object).itertuples(index=False, name=None):
            sheet.append([_clean(value) for value in row])
    temp = path.with_name(path.name + ".tmp")
    try:
        book.save(temp)
        os.replace(temp, path)
    except OSError:
        temp.unlink(missing_ok=True)
        raise
    return path


def read_workbook(path: Path) -> dict[str, pd.DataFrame]:
    """Every sheet of the workbook as strings ("" for an empty cell)."""
    return pd.read_excel(path, sheet_name=None, dtype=str, keep_default_na=False)

"""Data Set Chooser settings. Paths are absolute: set them for the machine this runs on."""

from __future__ import annotations

from pathlib import Path

# Raw page images to choose from: {Raw_Read_Path}/{RecordId}/{page image files}
Raw_Read_Path = Path(r"E:\Projects\NER\NER\Data\Raw")

# Selected records are copied here: {Selected_Path}/{RecordId}/{page image files}
Selected_Path = Path(r"E:\Projects\NER\NER\Data\Training_Set")

# Only records with N or fewer pages are listed (N = 5: records of 1 to 5 pages). 0 = no limit.
N = 10

for _name, _root in {"Raw_Read_Path": Raw_Read_Path, "Selected_Path": Selected_Path}.items():
    if not _root.is_absolute():
        raise SystemExit(f"config.{_name} must be an absolute path, got {_root}")

if Raw_Read_Path.resolve() == Selected_Path.resolve():
    raise SystemExit("config.Selected_Path must be a different folder from config.Raw_Read_Path")

if not isinstance(N, int) or N < 0:
    raise SystemExit(f"config.N must be a whole number, 0 or more, got {N!r}")

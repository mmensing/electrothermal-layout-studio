# SPDX-License-Identifier: MPL-2.0
"""Open the same offline HTML guide shipped with every installation."""
from importlib.resources import files
from pathlib import Path


def guide_path():
    from PySide6.QtCore import QStandardPaths
    path = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)) / "help" / "user-guide.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(files("etlayout").joinpath("data/user-guide.html").read_text(encoding="utf-8"), encoding="utf-8")
    return path

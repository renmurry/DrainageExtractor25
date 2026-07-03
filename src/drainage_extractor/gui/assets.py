"""Locate bundled resources (icons) in dev installs and PyInstaller bundles."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon


def resources_dir() -> Path:
    """Directory holding icon files (handles PyInstaller's _MEIPASS)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", ".")) / "resources"
    return Path(__file__).resolve().parent / "resources"


def app_icon() -> QIcon:
    """The application icon (empty QIcon if the file is missing)."""
    for name in ("icon.ico", "icon_256.png", "icon.svg"):
        p = resources_dir() / name
        if p.is_file():
            return QIcon(str(p))
    return QIcon()

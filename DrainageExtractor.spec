# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — one-file, windowed Windows build.

Build locally:
    pip install -e .[dev]
    python scripts/fetch_whitebox.py       # bundle the hydrology engine
    pyinstaller DrainageExtractor.spec --noconfirm

Output: dist/DrainageExtractor.exe
"""

import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve()  # noqa: F821 — provided by PyInstaller
SRC = ROOT / "src"
RES = SRC / "drainage_extractor" / "gui" / "resources"

_wbt_name = "whitebox_tools.exe" if sys.platform.startswith("win") else "whitebox_tools"
WBT = SRC / "drainage_extractor" / "bin" / _wbt_name

binaries = []
if WBT.exists():
    binaries.append((str(WBT), "wbt"))
else:
    print(f"WARNING: {WBT} not found — run scripts/fetch_whitebox.py before building.")
    print("         The exe will still work, using the slower built-in engine.")

a = Analysis(
    [str(SRC / "drainage_extractor" / "__main__.py")],
    pathex=[str(SRC)],
    binaries=binaries,
    datas=[(str(RES), "resources")],
    hiddenimports=[
        "rasterio.sample",
        "rasterio.vrt",
        "rasterio._features",
        "pyogrio._geometry",
        "pyogrio._io",
        "pyogrio._vsi",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "IPython", "jupyter", "PyQt5", "PyQt6"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="DrainageExtractor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                     # windowed
    icon=str(RES / "icon.ico"),
)

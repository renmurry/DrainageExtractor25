"""Download the WhiteboxTools binary for this platform into the package.

Usage:
    python scripts/fetch_whitebox.py [--dest PATH]

The binary lands in ``src/drainage_extractor/bin/`` (gitignored), where the
app, the tests and the PyInstaller spec all look for it. Standard library
only — no extra dependencies.
"""

from __future__ import annotations

import argparse
import io
import platform
import stat
import sys
import urllib.request
import zipfile
from pathlib import Path

BASE = "https://www.whiteboxgeo.com"
URLS: dict[str, str] = {
    "windows": f"{BASE}/WBT_Windows/WhiteboxTools_win_amd64.zip",
    "linux": f"{BASE}/WBT_Linux/WhiteboxTools_linux_amd64.zip",
    "darwin_x86": f"{BASE}/WBT_Darwin/WhiteboxTools_darwin_amd64.zip",
    "darwin_arm": f"{BASE}/WBT_Darwin/WhiteboxTools_darwin_m_series.zip",
}

DEFAULT_DEST = Path(__file__).resolve().parent.parent / "src" / "drainage_extractor" / "bin"


def platform_key() -> str:
    """Map this machine onto a WhiteboxTools download key."""
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "darwin_arm" if platform.machine().lower() in ("arm64", "aarch64") else "darwin_x86"
    return "linux"


def fetch(dest: Path) -> Path:
    """Download + extract the whitebox_tools executable into ``dest``."""
    key = platform_key()
    url = URLS[key]
    exe_name = "whitebox_tools.exe" if key == "windows" else "whitebox_tools"
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / exe_name

    print(f"Downloading WhiteboxTools ({key}) from {url} …")
    with urllib.request.urlopen(url, timeout=120) as resp:
        payload = resp.read()
    print(f"Downloaded {len(payload) / 1e6:.1f} MB, extracting {exe_name} …")

    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        member = next(
            (m for m in zf.namelist() if m.replace("\\", "/").endswith(f"/{exe_name}") or m == exe_name),
            None,
        )
        if member is None:
            raise SystemExit(f"Could not find {exe_name} inside the archive ({url}).")
        out.write_bytes(zf.read(member))

    if key != "windows":
        out.chmod(out.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"WhiteboxTools ready: {out}")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST, help="target directory")
    args = parser.parse_args()
    fetch(args.dest)


if __name__ == "__main__":
    main()

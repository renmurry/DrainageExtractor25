"""Application logging: rotating file log + console, one call at startup."""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

import platformdirs

APP_DIR_NAME = "DrainageExtractor"


def log_directory() -> Path:
    """Per-user log directory (e.g. %LOCALAPPDATA%/DrainageExtractor/Logs on Windows)."""
    return Path(platformdirs.user_log_dir(APP_DIR_NAME, appauthor=False))


def setup_logging(verbose: bool = False) -> Path:
    """Configure root logging to a rotating file and the console.

    Returns the log file path (shown in the GUI's About/error dialogs).
    Safe to call more than once — handlers are only added the first time.
    """
    root = logging.getLogger()
    if getattr(setup_logging, "_configured", False):
        return setup_logging._logfile  # type: ignore[return-value]

    log_dir = log_directory()
    log_dir.mkdir(parents=True, exist_ok=True)
    logfile = log_dir / "drainage_extractor.log"

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    file_handler = logging.handlers.RotatingFileHandler(
        logfile, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.DEBUG if verbose else logging.INFO)

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(fmt)
    console.setLevel(logging.INFO)

    root.setLevel(logging.DEBUG)
    root.addHandler(file_handler)
    root.addHandler(console)
    logging.getLogger("rasterio").setLevel(logging.WARNING)
    logging.getLogger("pyogrio").setLevel(logging.WARNING)
    logging.getLogger("fiona").setLevel(logging.WARNING)

    setup_logging._configured = True  # type: ignore[attr-defined]
    setup_logging._logfile = logfile  # type: ignore[attr-defined]
    logging.getLogger(__name__).info("Logging to %s", logfile)
    return logfile

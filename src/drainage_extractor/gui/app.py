"""Application entry point: ``drainage-extractor`` / ``python -m drainage_extractor``."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from drainage_extractor import APP_NAME, __version__
from drainage_extractor.utils.logging_setup import setup_logging

log = logging.getLogger(__name__)


def _windows_taskbar_identity() -> None:
    """Give the app its own taskbar icon group on Windows."""
    if os.name == "nt":  # pragma: no cover - Windows only
        import ctypes

        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                f"renmurry.DrainageExtractor.{__version__}"
            )
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    """Launch the GUI. Returns the process exit code."""
    parser = argparse.ArgumentParser(prog="drainage-extractor", description=APP_NAME)
    parser.add_argument("dem", nargs="?", help="DEM to open on startup (GeoTIFF/IMG/ASC)")
    parser.add_argument("--verbose", action="store_true", help="debug logging to the log file")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    args = parser.parse_args(argv)

    logfile = setup_logging(verbose=args.verbose)
    log.info("%s v%s starting (log: %s)", APP_NAME, __version__, logfile)
    _windows_taskbar_identity()

    from PySide6.QtWidgets import QApplication

    from drainage_extractor.gui import theme
    from drainage_extractor.gui.assets import app_icon
    from drainage_extractor.gui.dialogs import ErrorDialog
    from drainage_extractor.gui.main_window import MainWindow

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("renmurry")
    app.setStyle("Fusion")
    app.setStyleSheet(theme.STYLESHEET)
    app.setWindowIcon(app_icon())

    window = MainWindow()

    def excepthook(exc_type, exc, tb) -> None:  # pragma: no cover - GUI path
        import traceback

        logging.getLogger("unhandled").error(
            "Unhandled exception:\n%s", "".join(traceback.format_exception(exc_type, exc, tb))
        )
        try:
            exc.details = "".join(traceback.format_exception(exc_type, exc, tb))
        except Exception:
            pass
        ErrorDialog.show_error(exc, logfile, window)

    sys.excepthook = excepthook

    window.show()
    if args.dem:
        dem = Path(args.dem)
        if dem.exists():
            window.open_dem(dem)
        else:
            log.warning("DEM given on the command line does not exist: %s", dem)

    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

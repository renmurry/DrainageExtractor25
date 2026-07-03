"""Allow ``python -m drainage_extractor`` to launch the GUI."""

from drainage_extractor.gui.app import main

if __name__ == "__main__":
    raise SystemExit(main())

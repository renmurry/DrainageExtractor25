"""Offscreen GUI smoke test — window builds, loads a DEM, draws a network.

Skipped automatically when Qt's platform libraries are unavailable (e.g.
minimal CI containers without libEGL).
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6", reason="PySide6 not installed")

try:  # pragma: no cover - environment probe
    from PySide6.QtWidgets import QApplication
except ImportError as exc:  # missing system libGL/libEGL
    pytest.skip(f"Qt platform libraries unavailable: {exc}", allow_module_level=True)


@pytest.fixture(scope="module")
def qapp():
    from drainage_extractor.gui import theme

    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(theme.STYLESHEET)
    return app


def test_main_window_full_flow(qapp, sample_dem, pipeline_result) -> None:
    import rasterio

    from drainage_extractor.core.dem import load_dem_info, read_decimated
    from drainage_extractor.core.rasters import hillshade
    from drainage_extractor.gui.main_window import MainWindow

    w = MainWindow()
    w.resize(1100, 700)
    w.show()
    qapp.processEvents()

    info = load_dem_info(sample_dem)
    with rasterio.open(sample_dem) as ds:
        elev, tr = read_decimated(ds, 1024)
    w._dem_loaded((info, hillshade(elev, tr), tr))
    qapp.processEvents()
    assert w.canvas.has_image()
    assert w.panel.extract_btn.isEnabled()

    w._extract_done(pipeline_result)
    qapp.processEvents()
    assert w.panel.export_btn.isEnabled()
    shot = w.grab()
    assert not shot.isNull() and shot.width() > 0

    w.close()
    qapp.processEvents()


def test_export_dialog_crs_search(qapp, pipeline_result) -> None:
    from drainage_extractor.core.exports import VECTOR_FORMATS
    from drainage_extractor.gui.dialogs import ExportDialog

    dlg = ExportDialog("sample", pipeline_result.streams.crs, False, pipeline_result.workdir)
    dlg.crs_search.setText("32610")
    dlg._run_search()
    assert dlg.crs_results.count() >= 1
    dlg._pick_crs(dlg.crs_results.item(0))
    spec = dlg.spec()
    assert spec is not None and spec.dst_crs.to_epsg() == 32610

    # KML pins the CRS and disables the picker.
    dlg.format_combo.setCurrentIndex([f.key for f in VECTOR_FORMATS].index("kml"))
    assert not dlg.crs_search.isEnabled()

"""Main application window: canvas, docks, menus, worker orchestration."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import rasterio
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QScrollArea,
)

from drainage_extractor import APP_NAME, __version__
from drainage_extractor.core import streams as streams_mod
from drainage_extractor.core.dem import (
    SUPPORTED_EXTENSIONS,
    DEMInfo,
    load_dem_info,
    read_decimated,
    reproject_dem,
)
from drainage_extractor.core.engine import find_whitebox_binary
from drainage_extractor.core.errors import DrainageError
from drainage_extractor.core.exports import (
    export_hillshade,
    export_raster,
    export_vector_layers,
)
from drainage_extractor.core.pipeline import PipelineResult
from drainage_extractor.core.rasters import hillshade as make_hillshade
from drainage_extractor.core.watershed import delineate_watershed, snap_pour_point
from drainage_extractor.gui import theme
from drainage_extractor.gui.assets import app_icon
from drainage_extractor.gui.dialogs import (
    ErrorDialog,
    ExportDialog,
    ExportSpec,
    ReprojectDialog,
)
from drainage_extractor.gui.map_canvas import MapCanvas
from drainage_extractor.gui.panels import ParameterPanel, ProgressStrip, QtLogHandler
from drainage_extractor.gui.worker import FuncWorker, PipelineWorker
from drainage_extractor.utils.logging_setup import log_directory

log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """The Drainage Network Extractor desktop window."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(1280, 800)
        self.setAcceptDrops(True)

        self._dem_info: DEMInfo | None = None
        self._dem_path: Path | None = None
        self._result: PipelineResult | None = None
        self._watersheds: list[gpd.GeoDataFrame] = []
        self._grids = None
        self._worker = None
        self._run_counter = 0
        self._tmp = tempfile.TemporaryDirectory(prefix="drainage_gui_", ignore_cleanup_errors=True)

        # central canvas
        self.canvas = MapCanvas(self)
        self.setCentralWidget(self.canvas)
        self.canvas.map_clicked.connect(self._on_map_clicked)

        # left parameters dock
        self.panel = ParameterPanel(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.panel)
        dock = QDockWidget("Extraction", self)
        dock.setObjectName("paramsDock")
        dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        dock.setWidget(scroll)
        dock.setMinimumWidth(300)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)

        # bottom progress + log dock
        self.strip = ProgressStrip(self)
        bottom = QDockWidget("Progress", self)
        bottom.setObjectName("progressDock")
        bottom.setFeatures(QDockWidget.DockWidgetMovable)
        bottom.setTitleBarWidget(QLabelBlank())
        bottom.setWidget(self.strip)
        self.addDockWidget(Qt.BottomDockWidgetArea, bottom)

        self._wire_panel()
        self._build_menus()
        self._attach_log_console()

        wbt = find_whitebox_binary()
        self.panel.set_engine_note("WhiteboxTools" if wbt else "Built-in (NumPy)", wbt is not None)
        self.statusBar().showMessage(theme.VOICE_READY)

    # ------------------------------------------------------------------ setup
    def _wire_panel(self) -> None:
        self.panel.extract_requested.connect(self.extract)
        self.panel.cancel_requested.connect(self.cancel_current)
        self.panel.export_requested.connect(self.export_results)
        self.panel.watershed_mode_toggled.connect(self.canvas.set_pour_mode)
        self.panel.clear_watersheds_requested.connect(self._clear_watersheds)

    def _build_menus(self) -> None:
        m_file = self.menuBar().addMenu("&File")
        act_open = QAction("&Open DEM…", self)
        act_open.setShortcut(QKeySequence.Open)
        act_open.triggered.connect(self._open_dialog)
        act_export = QAction("&Export…", self)
        act_export.setShortcut(QKeySequence("Ctrl+E"))
        act_export.triggered.connect(self.export_results)
        act_quit = QAction("E&xit", self)
        act_quit.setShortcut(QKeySequence.Quit)
        act_quit.triggered.connect(self.close)
        m_file.addActions([act_open, act_export])
        m_file.addSeparator()
        m_file.addAction(act_quit)

        m_view = self.menuBar().addMenu("&View")
        act_fit = QAction("&Fit view", self)
        act_fit.setShortcut(QKeySequence("F"))
        act_fit.triggered.connect(self.canvas.fit_view)
        m_view.addAction(act_fit)

        m_help = self.menuBar().addMenu("&Help")
        act_about = QAction("&About", self)
        act_about.triggered.connect(self._about)
        m_help.addAction(act_about)

    def _attach_log_console(self) -> None:
        self._log_handler = QtLogHandler()
        self._log_handler.emitter.message.connect(self.strip.append_log)
        logging.getLogger().addHandler(self._log_handler)

    # ------------------------------------------------------------- DEM loading
    def _open_dialog(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(SUPPORTED_EXTENSIONS))
        path, _ = QFileDialog.getOpenFileName(self, "Open DEM", "", f"DEM rasters ({exts})")
        if path:
            self.open_dem(Path(path))

    def open_dem(self, path: Path) -> None:
        """Validate + preview a DEM on a worker thread."""
        if self._busy():
            return
        self.strip.set_busy(theme.VOICE_LOADING)
        self.statusBar().showMessage(theme.VOICE_LOADING)

        def job():
            info = load_dem_info(path)
            with rasterio.open(path) as ds:
                elev, tr = read_decimated(ds, max_size=2048)
            shade = make_hillshade(elev, tr, geographic=info.is_geographic)
            return info, shade, tr

        self._start(FuncWorker(job), self._dem_loaded, what="loading the DEM")

    def _dem_loaded(self, payload) -> None:
        info, shade, tr = payload
        self._dem_info = info
        self._dem_path = info.path
        self._result = None
        self._grids = None
        self._watersheds.clear()
        self.canvas.set_hillshade(shade, tr)
        self.panel.set_has_result(False)

        n_valid = int(info.n_cells * (info.valid_fraction or 1.0))
        self.panel.set_dem(info.summary(), info.cell_area_m2, streams_mod.suggest_threshold_cells(n_valid))
        self.setWindowTitle(f"{APP_NAME} — {info.path.name}")
        self.strip.set_idle()
        self.statusBar().showMessage(
            f"Loaded {info.path.name}: {info.width}×{info.height} @ {abs(info.res[0]):g} {info.linear_units}"
        )
        for w in info.warnings:
            log.warning("%s", w)

        if info.is_geographic:
            suggested = info.suggested_utm()
            if suggested is not None:
                dlg = ReprojectDialog(info.path.name, info.crs.name, suggested, self)
                if dlg.exec():
                    self._reproject(info, suggested)

    def _reproject(self, info: DEMInfo, dst_crs) -> None:
        out = Path(self._tmp.name) / f"{info.path.stem}_utm.tif"
        self.strip.set_busy(theme.VOICE_REPROJECT.format(crs=dst_crs.name))
        self._start(
            FuncWorker(reproject_dem, info.path, out, dst_crs, pass_cancel=True),
            lambda p: self.open_dem(Path(p)),
            what="reprojecting the DEM",
        )

    # -------------------------------------------------------------- extraction
    def extract(self) -> None:
        """Run the pipeline with the panel's parameters."""
        if self._dem_path is None or self._dem_info is None or self._busy():
            return
        params = self.panel.current_params()
        self._run_counter += 1
        workdir = Path(self._tmp.name) / f"run_{self._run_counter}"

        # session-scoped workdir so intermediates vanish when the app closes
        worker = PipelineWorker(self._dem_path, params, self._dem_info, workdir=workdir)
        self.panel.set_running(True)
        self.strip.set_progress(0.0, "Starting…")

        worker.progress.connect(
            lambda sid, frac, msg: self._on_progress(sid, frac, msg, params.preprocess)
        )
        worker.finished_ok.connect(self._extract_done)
        worker.cancelled_sig.connect(self._extract_cancelled)
        worker.failed.connect(lambda e: self._task_failed(e, "extracting the network"))
        self._worker = worker
        self._pipeline_workdir = workdir
        worker.start()

    def _on_progress(self, stage_id: str, fraction: float, message: str, preprocess: str) -> None:
        voice = theme.stage_voice(stage_id, preprocess)
        self.strip.set_progress(fraction, voice)
        self.statusBar().showMessage(message or voice)

    def _extract_done(self, result: PipelineResult) -> None:
        self._finish_worker()
        self._result = result
        self._grids = None
        self.panel.set_running(False)
        self.panel.set_has_result(True)
        self.panel.set_engine_note(result.engine_name, "whitebox" in result.engine_name.lower())
        self.canvas.set_streams(result.streams)
        summary = streams_mod.network_summary(result.streams)
        self.strip.set_idle(summary)
        self.statusBar().showMessage(summary)
        for w in result.warnings:
            log.warning("%s", w)

    def _extract_cancelled(self) -> None:
        self._finish_worker()
        self.panel.set_running(False)
        self.panel.set_has_result(self._result is not None)
        self.strip.set_idle(theme.VOICE_CANCELLED)
        self.statusBar().showMessage(theme.VOICE_CANCELLED)

    def cancel_current(self) -> None:
        if self._worker is not None:
            self.statusBar().showMessage("Cancelling…")
            self._worker.cancel()

    # -------------------------------------------------------------- watersheds
    def _on_map_clicked(self, x: float, y: float) -> None:
        if self._result is None or self._busy():
            return
        result = self._result
        ws_id = len(self._watersheds) + 1
        self.strip.set_busy(theme.VOICE_WATERSHED)

        def job():
            if self._grids is None:
                self._grids = result.load_grids()
            pointer, facc, valid, transform = self._grids
            rc = snap_pour_point(x, y, facc, valid, transform)
            if rc is None:
                raise DrainageError(
                    "That point is outside the DEM's data area.",
                    suggestion="Click on the hillshade, ideally near a blue stream line.",
                )
            gdf = delineate_watershed(
                pointer, valid, rc, transform, result.dem_info.crs,
                result.dem_info.cell_area_m2, point_id=ws_id,
            )
            ox, oy = transform * (rc[1] + 0.5, rc[0] + 0.5)
            return gdf, (float(ox), float(oy))

        def done(payload) -> None:
            gdf, snapped = payload
            self._watersheds.append(gdf)
            self.canvas.add_watershed(gdf)
            self.canvas.add_pour_marker(*snapped)
            area = float(gdf["area_km2"].iloc[0])
            msg = f"Watershed {ws_id}: {area:,.3f} km² — fenced and measured."
            self.strip.set_idle(msg)
            self.statusBar().showMessage(msg)

        self._start(FuncWorker(job), done, what="delineating the watershed")

    def _clear_watersheds(self) -> None:
        self._watersheds.clear()
        self.canvas.clear_watersheds()
        self.statusBar().showMessage("Watersheds cleared.")

    # ------------------------------------------------------------------ export
    def export_results(self) -> None:
        if self._result is None:
            self.statusBar().showMessage("Nothing to export yet — extract a network first.")
            return
        if self._busy():
            return
        result = self._result
        start_dir = result.dem_info.path.parent
        while True:
            dlg = ExportDialog(
                result.dem_info.path.stem,
                result.streams.crs,
                bool(self._watersheds),
                start_dir,
                self,
            )
            if not dlg.exec():
                return
            spec = dlg.spec()
            if spec is not None:
                break
            ErrorDialog.show_error(
                DrainageError(
                    "Pick a target CRS from the search results first.",
                    suggestion="Type an EPSG code or name, then click a result — or keep the DEM CRS.",
                ),
                parent=self,
            )

        watersheds = (
            gpd.GeoDataFrame(pd.concat(self._watersheds, ignore_index=True), crs=result.streams.crs)
            if self._watersheds
            else None
        )
        self.strip.set_busy(theme.VOICE_EXPORTING)
        self._start(FuncWorker(self._export_job, result, watersheds, spec), self._export_done,
                    what="exporting")

    @staticmethod
    def _export_job(result: PipelineResult, watersheds, spec: ExportSpec) -> list[Path]:
        layers = {"streams": result.streams}
        if spec.include_watersheds and watersheds is not None and not watersheds.empty:
            layers["watersheds"] = watersheds
        written = export_vector_layers(layers, spec.path, spec.format_key, dst_crs=spec.dst_crs)

        base = spec.path.with_suffix("")
        geographic = result.dem_info.is_geographic
        if spec.raster_conditioned:
            written.append(
                export_raster(result.conditioned_path, Path(f"{base}_conditioned_dem.tif"), spec.dst_crs)
            )
        if spec.raster_facc:
            written.append(
                export_raster(result.facc_path, Path(f"{base}_flow_accum.tif"), spec.dst_crs)
            )
        if spec.raster_hillshade:
            hs_tmp = result.workdir / "hillshade_export.tif"
            export_hillshade(result.conditioned_path, hs_tmp, geographic=geographic)
            written.append(export_raster(hs_tmp, Path(f"{base}_hillshade.tif"), spec.dst_crs))
        return written

    def _export_done(self, written: list[Path]) -> None:
        names = ", ".join(p.name for p in written)
        msg = f"Exported {len(written)} file(s): {names}"
        log.info("%s", msg)
        self.strip.set_idle(f"Exported {len(written)} file(s) — happy mapping.")
        self.statusBar().showMessage(msg)

    # ------------------------------------------------------------ infrastructure
    def _busy(self) -> bool:
        if self._worker is not None and self._worker.isRunning():
            self.statusBar().showMessage("Hold on — still working on the previous task.")
            return True
        return False

    def _start(self, worker: FuncWorker, on_done, what: str) -> None:
        worker.finished_ok.connect(lambda payload: (self._finish_worker(), on_done(payload)))
        worker.failed.connect(lambda e: self._task_failed(e, what))
        worker.cancelled_sig.connect(self._extract_cancelled)
        self._worker = worker
        worker.start()

    def _finish_worker(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
        self._worker = None

    def _task_failed(self, exc: Exception, what: str) -> None:
        self._finish_worker()
        self.panel.set_running(False)
        self.panel.set_has_result(self._result is not None)
        self.strip.set_idle("That didn't work — details in the dialog.")
        self.statusBar().showMessage(f"Problem while {what}.")
        ErrorDialog.show_error(exc, log_directory() / "drainage_extractor.log", self)

    def _about(self) -> None:
        wbt = find_whitebox_binary()
        QMessageBox.about(
            self,
            f"About {APP_NAME}",
            f"<b>{APP_NAME}</b> v{__version__}<br/>"
            "Extracts stream networks, Strahler orders and watersheds from DEMs.<br/><br/>"
            f"Hydrology engine: {'WhiteboxTools — ' + str(wbt) if wbt else 'Built-in (NumPy)'}<br/>"
            f"Log folder: {log_directory()}<br/><br/>"
            "MIT licensed — github.com/renmurry/DrainageExtractor25",
        )

    # --------------------------------------------------------------- drag&drop
    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if any(
            Path(u.toLocalFile()).suffix.lower() in SUPPORTED_EXTENSIONS
            for u in event.mimeData().urls()
        ):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        for u in event.mimeData().urls():
            p = Path(u.toLocalFile())
            if p.suffix.lower() in SUPPORTED_EXTENSIONS:
                self.open_dem(p)
                return

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape and self.panel.watershed_btn.isChecked():
            self.panel.watershed_btn.setChecked(False)
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(3000)
        logging.getLogger().removeHandler(self._log_handler)
        self._tmp.cleanup()
        super().closeEvent(event)


class QLabelBlank(QScrollArea):
    """Zero-height title bar for the bottom dock (keeps it slim)."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(0)
        self.setFrameShape(QScrollArea.NoFrame)

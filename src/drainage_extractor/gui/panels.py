"""Left parameter panel, DEM info box, progress strip and collapsible log console."""

from __future__ import annotations

import logging
import math

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from drainage_extractor.core.pipeline import PipelineParams

log = logging.getLogger(__name__)

_SLIDER_SCALE = 100  # slider holds log10(cells) * 100


class ParameterPanel(QWidget):
    """Extraction parameters + action buttons."""

    extract_requested = Signal()
    cancel_requested = Signal()
    export_requested = Signal()
    watershed_mode_toggled = Signal(bool)
    clear_watersheds_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cell_area_m2 = 25.0

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(10)

        # ---------------------------------------------------- preprocessing
        prep_box = QGroupBox("Preprocessing")
        prep_lay = QVBoxLayout(prep_box)
        self.method_combo = QComboBox()
        self.method_combo.addItem("Breach depressions (recommended)", "breach")
        self.method_combo.addItem("Fill sinks", "fill")
        self.method_combo.setToolTip(
            "Breaching carves through blockages (needs WhiteboxTools);\n"
            "filling raises pits until they overflow."
        )
        prep_lay.addWidget(self.method_combo)

        smooth_row = QHBoxLayout()
        self.smooth_check = QCheckBox("Smooth DEM first")
        self.smooth_check.setToolTip("Feature-preserving denoise for gritty lidar DEMs.")
        self.smooth_strength = QSlider(Qt.Horizontal)
        self.smooth_strength.setRange(1, 10)
        self.smooth_strength.setValue(3)
        self.smooth_strength.setEnabled(False)
        self.smooth_check.toggled.connect(self.smooth_strength.setEnabled)
        smooth_row.addWidget(self.smooth_check)
        smooth_row.addWidget(self.smooth_strength, 1)
        prep_lay.addLayout(smooth_row)
        root.addWidget(prep_box)

        # -------------------------------------------------------- threshold
        thr_box = QGroupBox("Stream threshold")
        thr_lay = QVBoxLayout(thr_box)
        self.threshold_slider = QSlider(Qt.Horizontal)
        self.threshold_slider.setRange(int(1.0 * _SLIDER_SCALE), int(6.0 * _SLIDER_SCALE))
        self.threshold_slider.setValue(int(2.58 * _SLIDER_SCALE))
        self.threshold_slider.setToolTip(
            "Cells that must drain through a cell before it becomes a stream.\n"
            "Lower → denser network; higher → only the main channels."
        )
        self.threshold_label = QLabel()
        self.threshold_label.setProperty("dim", True)
        self.threshold_slider.valueChanged.connect(self._update_threshold_label)
        thr_lay.addWidget(self.threshold_slider)
        thr_lay.addWidget(self.threshold_label)

        len_row = QHBoxLayout()
        len_row.addWidget(QLabel("Min stream length"))
        self.min_length = QDoubleSpinBox()
        self.min_length.setRange(0.0, 100_000.0)
        self.min_length.setSingleStep(10.0)
        self.min_length.setSuffix(" m")
        self.min_length.setValue(0.0)
        self.min_length.setToolTip("Prune first-order headwater links shorter than this.")
        len_row.addWidget(self.min_length, 1)
        thr_lay.addLayout(len_row)
        root.addWidget(thr_box)

        # ---------------------------------------------------------- actions
        self.extract_btn = QPushButton("Extract network")
        self.extract_btn.setObjectName("primary")
        self.extract_btn.setEnabled(False)
        self.extract_btn.clicked.connect(self.extract_requested)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setObjectName("danger")
        self.cancel_btn.hide()
        self.cancel_btn.clicked.connect(self.cancel_requested)

        self.watershed_btn = QPushButton("Pick watershed points")
        self.watershed_btn.setCheckable(True)
        self.watershed_btn.setEnabled(False)
        self.watershed_btn.setToolTip("Click the map to delineate the catchment above that point.")
        self.watershed_btn.toggled.connect(self.watershed_mode_toggled)

        self.clear_ws_btn = QPushButton("Clear watersheds")
        self.clear_ws_btn.setEnabled(False)
        self.clear_ws_btn.clicked.connect(self.clear_watersheds_requested)

        self.export_btn = QPushButton("Export…")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self.export_requested)

        root.addWidget(self.extract_btn)
        root.addWidget(self.cancel_btn)
        root.addWidget(self.watershed_btn)
        root.addWidget(self.clear_ws_btn)
        root.addWidget(self.export_btn)

        self.engine_label = QLabel()
        self.engine_label.setProperty("dim", True)
        self.engine_label.setWordWrap(True)
        root.addWidget(self.engine_label)

        # ---------------------------------------------------------- DEM info
        info_box = QGroupBox("DEM")
        info_lay = QVBoxLayout(info_box)
        self.info_label = QLabel("No DEM loaded.")
        self.info_label.setObjectName("demInfo")
        self.info_label.setWordWrap(True)
        self.info_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        info_lay.addWidget(self.info_label)
        root.addWidget(info_box, 1)
        root.addStretch(0)

        self._update_threshold_label()

    # -------------------------------------------------------------- helpers
    def _update_threshold_label(self) -> None:
        cells = self.threshold_cells()
        area_km2 = cells * self._cell_area_m2 / 1e6
        unit = f"{area_km2:,.3f} km²" if area_km2 >= 0.01 else f"{cells * self._cell_area_m2:,.0f} m²"
        self.threshold_label.setText(f"{cells:,} cells  ≈  {unit} drainage area")

    def threshold_cells(self) -> int:
        return int(round(10 ** (self.threshold_slider.value() / _SLIDER_SCALE)))

    def set_threshold_cells(self, cells: int) -> None:
        self.threshold_slider.setValue(int(round(math.log10(max(10, cells)) * _SLIDER_SCALE)))

    def set_dem(self, summary: str, cell_area_m2: float, suggested_threshold: int) -> None:
        """Update the info box and re-centre the threshold slider for this DEM."""
        self.info_label.setText(summary)
        self._cell_area_m2 = max(1e-9, cell_area_m2)
        self.set_threshold_cells(suggested_threshold)
        self._update_threshold_label()
        self.extract_btn.setEnabled(True)

    def set_engine_note(self, name: str, supports_breaching: bool) -> None:
        note = f"Engine: {name}"
        if not supports_breaching:
            note += " — install WhiteboxTools to enable breaching (scripts/fetch_whitebox.py)"
        self.engine_label.setText(note)

    def current_params(self) -> PipelineParams:
        """Collect the panel state into pipeline parameters."""
        return PipelineParams(
            preprocess=self.method_combo.currentData(),
            smooth=self.smooth_check.isChecked(),
            smooth_strength=self.smooth_strength.value(),
            threshold_cells=self.threshold_cells(),
            min_stream_length_m=self.min_length.value(),
        )

    def set_running(self, running: bool) -> None:
        """Flip the panel between idle and busy states.

        Callers must invoke :meth:`set_has_result` after ``set_running(False)``
        to restore the result-dependent buttons correctly.
        """
        idle = not running
        for w in (
            self.extract_btn, self.method_combo, self.smooth_check,
            self.threshold_slider, self.min_length,
        ):
            w.setEnabled(idle)
        self.smooth_strength.setEnabled(idle and self.smooth_check.isChecked())
        for w in (self.export_btn, self.watershed_btn, self.clear_ws_btn):
            w.setEnabled(False)
        if running:
            self.watershed_btn.setChecked(False)
        self.cancel_btn.setVisible(running)

    def set_has_result(self, has: bool) -> None:
        self.export_btn.setEnabled(has)
        self.watershed_btn.setEnabled(has)
        self.clear_ws_btn.setEnabled(has)


class ProgressStrip(QWidget):
    """Bottom strip: stage label + progress bar + collapsible log console."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 4, 10, 6)
        root.setSpacing(4)

        top = QHBoxLayout()
        self.stage_label = QLabel("")
        self.stage_label.setProperty("dim", True)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setFixedHeight(16)
        self.bar.setTextVisible(False)
        self.toggle_btn = QToolButton()
        self.toggle_btn.setText("Log")
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setArrowType(Qt.RightArrow)
        self.toggle_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle_btn.toggled.connect(self._toggle_log)
        top.addWidget(self.stage_label, 1)
        top.addWidget(self.bar, 2)
        top.addWidget(self.toggle_btn)
        root.addLayout(top)

        self.console = QPlainTextEdit()
        self.console.setObjectName("logConsole")
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(3000)
        self.console.setFixedHeight(150)
        self.console.hide()
        root.addWidget(self.console)

    def _toggle_log(self, checked: bool) -> None:
        self.toggle_btn.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)
        self.console.setVisible(checked)

    # ---------------------------------------------------------------- slots
    def set_progress(self, fraction: float, stage_text: str) -> None:
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(True)
        self.bar.setValue(int(round(fraction * 100)))
        self.stage_label.setText(stage_text)

    def set_busy(self, text: str) -> None:
        """Indeterminate mode for un-metered tasks (exports, watershed)."""
        self.bar.setRange(0, 0)
        self.stage_label.setText(text)

    def set_idle(self, text: str = "") -> None:
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.stage_label.setText(text)

    def append_log(self, line: str) -> None:
        self.console.appendPlainText(line)


class QtLogHandler(logging.Handler):
    """Bridges Python logging into the GUI console via a queued signal.

    Workers log from background threads; the signal/slot hop makes the
    console append happen safely on the GUI thread.
    """

    class _Emitter(QObject):
        message = Signal(str)

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.emitter = QtLogHandler._Emitter()
        self.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102
        try:
            self.emitter.message.emit(self.format(record))
        except RuntimeError:  # emitter deleted at shutdown
            pass

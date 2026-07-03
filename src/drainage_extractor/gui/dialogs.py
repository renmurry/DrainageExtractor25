"""Dialogs: export (with EPSG search), plain-language errors, reprojection prompt."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from pyproj import CRS
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QTextEdit,
    QVBoxLayout,
)

from drainage_extractor.core.crs_utils import CRSCandidate, describe_crs, search_crs
from drainage_extractor.core.errors import DrainageError
from drainage_extractor.core.exports import VECTOR_FORMATS

log = logging.getLogger(__name__)


# ------------------------------------------------------------------- errors
class ErrorDialog(QDialog):
    """Plain-language error with an optional technical-details expander."""

    def __init__(self, title: str, exc: Exception, log_path: Path | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)

        if isinstance(exc, DrainageError):
            message, suggestion, details = exc.user_message, exc.suggestion, exc.details
        else:
            message = "Something unexpected went wrong."
            suggestion = "Trying again often helps. If it keeps happening, check the log file."
            details = getattr(exc, "details", "") or repr(exc)

        head = QLabel(f"<b>{message}</b>")
        head.setWordWrap(True)
        lay.addWidget(head)
        if suggestion:
            tip = QLabel(suggestion)
            tip.setWordWrap(True)
            tip.setProperty("dim", True)
            lay.addWidget(tip)
        if log_path:
            path_label = QLabel(f"Log file: {log_path}")
            path_label.setProperty("dim", True)
            path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lay.addWidget(path_label)

        if details:
            self._details = QTextEdit()
            self._details.setReadOnly(True)
            self._details.setPlainText(str(details))
            self._details.setFixedHeight(140)
            self._details.hide()
            toggle = QPushButton("Show details")
            toggle.setCheckable(True)

            def flip(checked: bool) -> None:
                self._details.setVisible(checked)
                toggle.setText("Hide details" if checked else "Show details")
                self.adjustSize()

            toggle.toggled.connect(flip)
            lay.addWidget(toggle)
            lay.addWidget(self._details)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        lay.addWidget(buttons)

    @staticmethod
    def show_error(exc: Exception, log_path: Path | None = None, parent=None, title: str = "Problem") -> None:
        ErrorDialog(title, exc, log_path, parent).exec()


# --------------------------------------------------------------- reproject
class ReprojectDialog(QDialog):
    """Warn about a geographic CRS and offer auto-reprojection to UTM."""

    def __init__(self, dem_name: str, current: str, suggested: CRS, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Geographic coordinates detected")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        msg = QLabel(
            f"<b>{dem_name}</b> uses geographic coordinates ({current}).<br/><br/>"
            "Flow directions and drainage areas computed in degrees come out distorted. "
            f"Reprojecting to <b>{suggested.name}</b> (EPSG:{suggested.to_epsg()}) first is "
            "strongly recommended."
        )
        msg.setWordWrap(True)
        lay.addWidget(msg)
        buttons = QDialogButtonBox()
        self.reproject_btn = buttons.addButton("Reproject (recommended)", QDialogButtonBox.AcceptRole)
        buttons.addButton("Keep degrees anyway", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)


# ------------------------------------------------------------------ export
@dataclass
class ExportSpec:
    """Everything the export worker needs."""

    path: Path
    format_key: str = "gpkg"
    dst_crs: CRS | None = None          # None → keep source CRS
    include_watersheds: bool = True
    raster_conditioned: bool = False
    raster_facc: bool = False
    raster_hillshade: bool = False
    extra: dict = field(default_factory=dict)


class ExportDialog(QDialog):
    """Choose format, destination, CRS (searchable EPSG) and raster add-ons."""

    def __init__(
        self,
        dem_stem: str,
        source_crs: CRS | None,
        has_watersheds: bool,
        start_dir: Path,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export results")
        self.setMinimumWidth(520)
        self._source_crs = source_crs
        self._selected: CRSCandidate | None = None
        self._start_dir = start_dir
        self._dem_stem = dem_stem

        lay = QVBoxLayout(self)

        # format + path
        fmt_row = QHBoxLayout()
        fmt_row.addWidget(QLabel("Format"))
        self.format_combo = QComboBox()
        for fmt in VECTOR_FORMATS:
            self.format_combo.addItem(fmt.label, fmt.key)
        fmt_row.addWidget(self.format_combo, 1)
        lay.addLayout(fmt_row)

        path_row = QHBoxLayout()
        self.path_edit = QLineEdit(str(start_dir / f"streams_{dem_stem}.gpkg"))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(browse)
        lay.addLayout(path_row)

        # CRS
        crs_box = QGroupBox("Coordinate reference system")
        crs_lay = QVBoxLayout(crs_box)
        self.keep_radio = QRadioButton(f"Keep DEM CRS — {describe_crs(source_crs)}")
        self.keep_radio.setChecked(True)
        self.reproj_radio = QRadioButton("Reproject to:")
        crs_lay.addWidget(self.keep_radio)
        crs_lay.addWidget(self.reproj_radio)

        self.crs_search = QLineEdit()
        self.crs_search.setPlaceholderText("Search EPSG by code or name… e.g. 32610 or 'UTM zone 10N'")
        self.crs_results = QListWidget()
        self.crs_results.setFixedHeight(120)
        crs_lay.addWidget(self.crs_search)
        crs_lay.addWidget(self.crs_results)
        self.fixed_note = QLabel("")
        self.fixed_note.setProperty("dim", True)
        self.fixed_note.setWordWrap(True)
        crs_lay.addWidget(self.fixed_note)
        lay.addWidget(crs_box)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(220)
        self.crs_search.textEdited.connect(lambda _t: self._search_timer.start())
        self._search_timer.timeout.connect(self._run_search)
        self.crs_search.textEdited.connect(lambda _t: self.reproj_radio.setChecked(True))
        self.crs_results.itemClicked.connect(self._pick_crs)

        # add-ons
        add_box = QGroupBox("Also export")
        add_lay = QVBoxLayout(add_box)
        self.ws_check = QCheckBox("Watershed polygons")
        self.ws_check.setChecked(has_watersheds)
        self.ws_check.setEnabled(has_watersheds)
        self.cond_check = QCheckBox("Conditioned (breached/filled) DEM — GeoTIFF")
        self.facc_check = QCheckBox("Flow accumulation — GeoTIFF")
        self.hs_check = QCheckBox("Hillshade — GeoTIFF")
        for c in (self.ws_check, self.cond_check, self.facc_check, self.hs_check):
            add_lay.addWidget(c)
        lay.addWidget(add_box)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        self.format_combo.currentIndexChanged.connect(self._format_changed)
        self._format_changed()

    # ---------------------------------------------------------------- slots
    def _format_changed(self) -> None:
        fmt = next(f for f in VECTOR_FORMATS if f.key == self.format_combo.currentData())
        p = Path(self.path_edit.text().strip() or (self._start_dir / f"streams_{self._dem_stem}"))
        self.path_edit.setText(str(p.with_suffix(fmt.extension)))
        pinned = fmt.fixed_crs is not None
        for w in (self.keep_radio, self.reproj_radio, self.crs_search, self.crs_results):
            w.setEnabled(not pinned)
        self.fixed_note.setText(
            f"{fmt.label.split(' (')[0]} is always written in WGS 84 (EPSG:{fmt.fixed_crs})." if pinned else ""
        )

    def _browse(self) -> None:
        fmt = next(f for f in VECTOR_FORMATS if f.key == self.format_combo.currentData())
        path, _ = QFileDialog.getSaveFileName(self, "Export to", self.path_edit.text(), fmt.label)
        if path:
            self.path_edit.setText(str(Path(path).with_suffix(fmt.extension)))

    def _run_search(self) -> None:
        self.crs_results.clear()
        for cand in search_crs(self.crs_search.text(), limit=40):
            item = QListWidgetItem(cand.label)
            item.setData(Qt.UserRole, cand)
            self.crs_results.addItem(item)

    def _pick_crs(self, item: QListWidgetItem) -> None:
        self._selected = item.data(Qt.UserRole)
        self.reproj_radio.setChecked(True)
        self.crs_search.setText(self._selected.label)

    # ----------------------------------------------------------------- spec
    def spec(self) -> ExportSpec | None:
        """Build the spec from the dialog state (None when input is incomplete)."""
        text = self.path_edit.text().strip()
        if not text:
            return None
        dst: CRS | None = None
        if self.reproj_radio.isChecked() and self.reproj_radio.isEnabled():
            if self._selected is None:
                return None
            dst = self._selected.to_crs()
        return ExportSpec(
            path=Path(text),
            format_key=self.format_combo.currentData(),
            dst_crs=dst,
            include_watersheds=self.ws_check.isChecked(),
            raster_conditioned=self.cond_check.isChecked(),
            raster_facc=self.facc_check.isChecked(),
            raster_hillshade=self.hs_check.isChecked(),
        )

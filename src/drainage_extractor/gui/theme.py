"""Dark theme (QSS + palette), Strahler colour ramp, and status-bar voice."""

from __future__ import annotations

from PySide6.QtGui import QColor

# ----------------------------------------------------------------- palette
BG = "#171a1f"          # window
PANEL = "#20242b"       # docks, group boxes
PANEL_ALT = "#262b33"
BORDER = "#333a45"
TEXT = "#e8eaed"
TEXT_DIM = "#9aa3ad"
ACCENT = "#4fc3f7"      # water blue
ACCENT_DARK = "#2286b8"
DANGER = "#ef5350"
CONSOLE_BG = "#101317"

#: Stream colours by Strahler order (1-based), light → deep water blue.
ORDER_COLORS: tuple[str, ...] = (
    "#8ed4ff", "#5ebdf7", "#38a5ee", "#2288d8", "#1a6fc0", "#1857a6", "#164189", "#12306b",
)


def order_color(order: int) -> QColor:
    """Colour for a Strahler order (clamped to the ramp)."""
    idx = max(0, min(len(ORDER_COLORS) - 1, int(order) - 1))
    return QColor(ORDER_COLORS[idx])


def order_width(order: int) -> float:
    """Cosmetic pen width for a Strahler order."""
    return 1.0 + 0.65 * (max(1, int(order)) - 1)


# ------------------------------------------------------------- status voice
#: Fun-but-professional status lines, keyed by pipeline stage id.
STAGE_VOICE: dict[str, str] = {
    "prepare": "Sizing up the terrain…",
    "smooth": "Ironing out the noise…",
    "condition_breach": "Breaching depressions — water always finds a way",
    "condition_fill": "Filling sinks — no puddle left behind",
    "pointer": "Asking every cell which way is down…",
    "facc": "Counting every raindrop's commute…",
    "extract": "Tracing the blue lines…",
}

VOICE_LOADING = "Reading the lay of the land…"
VOICE_REPROJECT = "Re-mapping to {crs} — straightening the graticule…"
VOICE_CANCELLED = "Stopped. The rivers will wait."
VOICE_EXPORTING = "Packing your rivers to go…"
VOICE_WATERSHED = "Fencing off the catchment…"
VOICE_READY = "Ready. Drop a DEM to begin."


def stage_voice(stage_id: str, preprocess: str) -> str:
    """Status line for a stage (conditioning line depends on the chosen method)."""
    if stage_id == "condition":
        return STAGE_VOICE["condition_breach" if preprocess == "breach" else "condition_fill"]
    return STAGE_VOICE.get(stage_id, "Working…")


# ---------------------------------------------------------------------- QSS
STYLESHEET = f"""
QMainWindow, QDialog {{ background: {BG}; }}
QWidget {{ color: {TEXT}; font-size: 13px; }}

QScrollArea {{ background: {BG}; border: none; }}
QScrollArea > QWidget > QWidget {{ background: {BG}; }}
QAbstractScrollArea::corner {{ background: {BG}; }}

QDockWidget::title {{
    background: {PANEL}; padding: 6px 10px; border-bottom: 1px solid {BORDER};
    font-weight: 600;
}}

QGroupBox {{
    background-color: {PANEL}; border: 1px solid {BORDER}; border-radius: 8px;
    margin-top: 14px; padding: 10px 8px 8px 8px; font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin; left: 10px; top: 1px; padding: 0 4px; color: {ACCENT};
}}

QLabel {{ background: transparent; }}
QLabel#demInfo {{ color: {TEXT_DIM}; font-family: Consolas, 'Cascadia Mono', monospace; font-size: 12px; }}
QLabel[dim="true"] {{ color: {TEXT_DIM}; }}
QCheckBox, QRadioButton {{ background: transparent; }}

QPushButton {{
    background: {PANEL_ALT}; border: 1px solid {BORDER}; border-radius: 6px;
    padding: 7px 14px;
}}
QPushButton:hover {{ border-color: {ACCENT_DARK}; background: #2c323c; }}
QPushButton:pressed {{ background: #1b1f26; }}
QPushButton:disabled {{ color: #5c646e; background: {PANEL}; }}
QPushButton#primary {{
    background: {ACCENT_DARK}; border-color: {ACCENT_DARK}; color: white; font-weight: 600;
}}
QPushButton#primary:hover {{ background: #2b9ad2; }}
QPushButton#primary:disabled {{ background: {PANEL_ALT}; color: #5c646e; }}
QPushButton#danger {{ color: {DANGER}; }}
QPushButton:checked {{ background: {ACCENT_DARK}; color: white; }}

QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {{
    background: {PANEL_ALT}; border: 1px solid {BORDER}; border-radius: 6px;
    padding: 5px 8px; selection-background-color: {ACCENT_DARK};
}}
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {{ border-color: {ACCENT}; }}
QComboBox QAbstractItemView {{
    background: {PANEL_ALT}; border: 1px solid {BORDER};
    selection-background-color: {ACCENT_DARK};
}}

QSlider {{ background: transparent; }}
QSlider::groove:horizontal {{ height: 5px; background: {BORDER}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    width: 16px; height: 16px; margin: -6px 0; border-radius: 8px; background: {ACCENT};
}}
QSlider::sub-page:horizontal {{ background: {ACCENT_DARK}; border-radius: 2px; }}

QProgressBar {{
    background: {PANEL_ALT}; border: 1px solid {BORDER}; border-radius: 6px;
    text-align: center; color: {TEXT}; height: 18px;
}}
QProgressBar::chunk {{ background: {ACCENT_DARK}; border-radius: 5px; }}

QPlainTextEdit#logConsole {{
    background: {CONSOLE_BG}; border: 1px solid {BORDER}; border-radius: 6px;
    color: #b7c1cb; font-family: Consolas, 'Cascadia Mono', monospace; font-size: 12px;
}}
QTextEdit {{ background: {CONSOLE_BG}; border: 1px solid {BORDER}; color: #b7c1cb; }}

QListWidget {{
    background: {PANEL_ALT}; border: 1px solid {BORDER}; border-radius: 6px;
}}
QListWidget::item {{ padding: 4px 6px; }}
QListWidget::item:selected {{ background: {ACCENT_DARK}; color: white; }}

QStatusBar {{ background: {PANEL}; border-top: 1px solid {BORDER}; color: {TEXT_DIM}; }}
QMenuBar {{ background: {BG}; }}
QMenuBar::item:selected {{ background: {PANEL_ALT}; }}
QMenu {{ background: {PANEL_ALT}; border: 1px solid {BORDER}; }}
QMenu::item:selected {{ background: {ACCENT_DARK}; }}
QToolTip {{ background: {PANEL_ALT}; color: {TEXT}; border: 1px solid {BORDER}; }}
QToolButton {{ background: {PANEL_ALT}; border: 1px solid {BORDER}; border-radius: 5px; padding: 3px 8px; }}
QToolButton:hover {{ border-color: {ACCENT_DARK}; }}

QCheckBox::indicator, QRadioButton::indicator {{ width: 15px; height: 15px; }}
QScrollBar:vertical {{ background: {BG}; width: 11px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {ACCENT_DARK}; }}
QScrollBar:horizontal {{ background: {BG}; height: 11px; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 5px; min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QSplitter::handle {{ background: {BORDER}; }}
"""

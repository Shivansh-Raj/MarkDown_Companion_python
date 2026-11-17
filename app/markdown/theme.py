"""Themes: assets/themes/<id>.json holds a colour palette; CSS and Qt stylesheet are generated from it.

Adding a theme = dropping a new JSON file next to retro.json.
"""
from __future__ import annotations

from PySide6.QtGui import QColor

import base64
import json
from functools import lru_cache

from ..utils.paths import resource_dir

DEFAULT_THEME = "retro"


def themes_dir():
    return resource_dir() / "themes"


def list_themes() -> dict[str, str]:
    """id -> display name."""
    out = {}
    for f in sorted(themes_dir().glob("*.json")):
        try:
            out[f.stem] = json.loads(f.read_text("utf-8")).get("name", f.stem)
        except (OSError, json.JSONDecodeError):
            continue
    return out or {DEFAULT_THEME: "Retro Charcoal"}


@lru_cache(maxsize=None)
def load_theme(theme_id: str) -> dict:
    try:
        return json.loads((themes_dir() / f"{theme_id}.json").read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        if theme_id != DEFAULT_THEME:
            return load_theme(DEFAULT_THEME)
        raise


def _font_uri(weight: int) -> str:
    f = resource_dir() / "fonts" / f"silkscreen-latin-{weight}-normal.woff2"
    try:
        return "data:font/woff2;base64," + base64.b64encode(f.read_bytes()).decode()
    except OSError:
        return ""


@lru_cache(maxsize=None)
def content_css(theme_id: str) -> str:
    from .renderer import pygments_css

    t = load_theme(theme_id)
    root = ":root{" + "".join(f"--{k}:{v};" for k, v in t.items() if k not in ("name", "pygments")) + "}"
    base = (themes_dir() / "base.css").read_text("utf-8")
    base = base.replace("{{font400}}", _font_uri(400)).replace("{{font700}}", _font_uri(700))
    return root + base + pygments_css(t.get("pygments", "native"))


_QSS = """
/* ---- frame: thick double-line pixel border, like V1 ---- */
QWidget#root { background: %(shadow)s; }
QWidget#frame { background: %(bg)s; border: 3px solid %(border_hi)s; }
QWidget#titlebar { background: %(accent)s; border-bottom: 3px solid %(border_hi)s; }
QLabel#title { color: %(accent_ink)s; font-family: Consolas, Menlo, monospace; font-weight: bold; font-size: 12px; letter-spacing: 1px; }
QLabel#status { background: #5a2b2b; color: #ffd9d9; padding: 4px 8px; font-size: 11px; border-bottom: 2px solid #8a3b3b; }
QLabel#status[kind="info"] { background: %(bg_alt)s; color: %(accent2)s; border-bottom: 2px solid %(border)s; }

/* ---- 3D buttons: bevelled with a hard shadow edge; the bevel flips when pressed ---- */
QToolButton#tbtn { color: %(fg)s; background: %(bg)s; font-family: Consolas, Menlo, monospace; font-weight: bold; font-size: 11px; padding: 0;
  border: 2px solid %(border_hi)s; }
QToolButton#tbtn:hover { background: %(code_bg)s; color: %(accent)s; }
QToolButton#tbtn:pressed { background: %(bg_alt)s; }
QToolButton#tbtn:disabled { color: %(border)s; }
QToolButton#tbtn[role="close"]:hover { background: #8a3b3b; color: white; }
QPushButton { background: %(bg_alt)s; color: %(fg)s; padding: 4px 12px; font-weight: bold;
  border: 2px solid %(border_hi)s; }
QPushButton:hover { background: %(code_bg)s; color: %(accent)s; }
QPushButton:pressed { background: %(bg)s; }
QPushButton:disabled { color: %(muted)s; background: %(bg)s; border-color: %(border)s; }
QPushButton#primary { background: %(accent)s; color: %(accent_ink)s; padding: 6px 14px; }
QPushButton#primary:hover { background: %(accent)s; color: %(accent_ink)s; border-top-color: %(accent_ink)s; border-left-color: %(accent_ink)s; }
QPushButton#primary:pressed { background: %(accent)s; }
QPushButton#primary:disabled { background: %(bg_alt)s; color: %(muted)s; }
QWidget#editbar QPushButton { padding: 2px 8px; }

/* ---- bars, panels, lists ---- */
QWidget#home { background: %(bg)s; }
QWidget#home QPushButton { font-size: 13px; letter-spacing: 1px; }
QWidget#home QPushButton#primary { font-size: 15px; letter-spacing: 2px; }
QWidget#home QPushButton#primary:pressed { background: %(accent)s; }
QLabel#tagline { color: %(accent2)s; font-family: Consolas, Menlo, monospace; font-size: 11px; letter-spacing: 3px; }
QLabel#hero { padding-bottom: 0; }
QWidget#footbar { border-top: 3px double %(border)s; }
QLabel#hero { color: %(accent)s; font-family: Consolas, Menlo, monospace; font-weight: bold; font-size: 26px; letter-spacing: 3px; }
QLabel#hint { color: %(muted)s; font-size: 11px; } QLabel#hint[warn="true"] { color: #ff9b6b; }
QLabel#section { color: %(accent2)s; font-weight: bold; font-size: 11px; letter-spacing: 2px; margin-top: 6px; border-bottom: 3px double %(border)s; padding-bottom: 3px; }
QListWidget { background: %(code_bg)s; color: %(fg)s; outline: 0; font-family: Consolas, Menlo, monospace; padding: 2px;
  border-top: 3px solid %(shadow)s; border-left: 3px solid %(shadow)s; border-right: 2px solid %(border_hi)s; border-bottom: 2px solid %(border_hi)s; }
QListWidget::item { padding: 6px 8px; border-bottom: 1px dashed %(border)s; } QListWidget::item:hover { background: %(bg_alt)s; }
QListWidget::item:selected { background: %(accent)s; color: %(accent_ink)s; }
QTabWidget::pane { border: 2px solid %(border_hi)s; background: %(bg)s; top: -2px; }
QTabBar::tab { background: %(bg_alt)s; color: %(fg)s; padding: 4px 10px; margin-right: 2px; font-weight: bold;
  border: 2px solid %(border_hi)s; border-bottom: 0; }
QTabBar::tab:selected { background: %(accent)s; color: %(accent_ink)s; }
QTabBar::tab:hover:!selected { background: %(code_bg)s; color: %(accent)s; }
QWidget#viewbox { background: %(bg)s; }
QWidget#progress { background: %(bg)s; border-bottom: 3px solid %(border_hi)s; }
QProgressBar#taskbar { background: %(code_bg)s; border-top: 3px solid %(shadow)s; border-left: 3px solid %(shadow)s;
  border-right: 2px solid %(border_hi)s; border-bottom: 2px solid %(border_hi)s; }
QProgressBar#taskbar::chunk { background: %(accent)s; }
QProgressBar#taskbar[complete="true"]::chunk { background: %(accent2)s; }
QLabel#progresslabel { color: %(fg)s; font-family: Consolas, Menlo, monospace; font-weight: bold; font-size: 11px; }
QWidget#editbar { background: %(bg_alt)s; border-bottom: 3px solid %(border_hi)s; }
QPlainTextEdit#editor { background: %(code_bg)s; color: %(fg)s; border: 0; padding: 8px; font-family: Consolas, Menlo, monospace;
  font-size: 13px; selection-background-color: %(accent)s; selection-color: %(accent_ink)s; }
QTextEdit#editor, QTextBrowser#preview { background: %(code_bg)s; color: %(fg)s; padding: 6px; font-size: 13px;
  selection-background-color: %(accent)s; selection-color: %(accent_ink)s;
  border-top: 3px solid %(shadow)s; border-left: 3px solid %(shadow)s; border-right: 2px solid %(border_hi)s; border-bottom: 2px solid %(border_hi)s; }
QScrollArea#aiscroll, QScrollArea#aiscroll > QWidget > QWidget { background: transparent; border: 0; }
QScrollBar:vertical { background: %(bg_alt)s; width: 14px; margin: 0; border-left: 2px solid %(border)s; }
QScrollBar::handle:vertical { background: %(border_hi)s; min-height: 24px; margin: 2px; border-right: 2px solid %(shadow)s; border-bottom: 2px solid %(shadow)s; }
QScrollBar::handle:vertical:hover { background: %(accent)s; }
QScrollBar:horizontal { background: %(bg_alt)s; height: 14px; margin: 0; border-top: 2px solid %(border)s; }
QScrollBar::handle:horizontal { background: %(border_hi)s; min-width: 24px; margin: 2px; border-right: 2px solid %(shadow)s; border-bottom: 2px solid %(shadow)s; }
QScrollBar::handle:horizontal:hover { background: %(accent)s; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; } QScrollBar::add-page, QScrollBar::sub-page { background: none; }

/* ---- dialogs and form controls ---- */
QDialog, QMenu { background: %(bg)s; color: %(fg)s; font-family: Consolas, Menlo, monospace; }
QMenu { border: 3px solid %(border_hi)s; } QMenu::item { padding: 5px 22px; }
QMenu::item:selected { background: %(accent)s; color: %(accent_ink)s; } QMenu::separator { height: 2px; background: %(border)s; }
QDialog QLabel, QCheckBox, QRadioButton { color: %(fg)s; }
QLineEdit, QKeySequenceEdit { background: %(code_bg)s; color: %(fg)s; padding: 4px 6px; selection-background-color: %(accent)s; selection-color: %(accent_ink)s;
  border-top: 3px solid %(shadow)s; border-left: 3px solid %(shadow)s; border-right: 2px solid %(border_hi)s; border-bottom: 2px solid %(border_hi)s; }
QComboBox { background: %(bg_alt)s; color: %(fg)s; padding: 3px 8px; font-weight: bold;
  border: 2px solid %(border_hi)s; }
QComboBox QAbstractItemView { background: %(code_bg)s; color: %(fg)s; border: 2px solid %(border_hi)s; selection-background-color: %(accent)s; selection-color: %(accent_ink)s; }
QCheckBox::indicator, QRadioButton::indicator { width: 14px; height: 14px; background: %(code_bg)s; border: 2px solid %(border_hi)s; }
QCheckBox::indicator:checked, QRadioButton::indicator:checked { background: %(accent)s; border: 2px solid %(border_hi)s; }
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled { border-color: %(border)s; }
QRadioButton::indicator { border-radius: 8px; }
QSlider::groove:horizontal { height: 8px; background: %(code_bg)s; border: 2px solid %(border_hi)s; }
QSlider::sub-page:horizontal { background: %(accent)s; }
QSlider::handle:horizontal { background: %(bg_alt)s; width: 12px; margin: -6px 0; border: 2px solid %(border_hi)s; border-right: 3px solid %(shadow)s; border-bottom: 3px solid %(shadow)s; }
"""


# classic bevelled buttons (thick shadow edge, flipped when pressed); appended when V1-style borders are off
_BEVEL = """
QToolButton#tbtn { border-top: 2px solid %(border_hi)s; border-left: 2px solid %(border_hi)s;
  border-right: 3px solid %(shadow)s; border-bottom: 3px solid %(shadow)s; }
QToolButton#tbtn:pressed { border-top: 3px solid %(shadow)s; border-left: 3px solid %(shadow)s;
  border-right: 2px solid %(border_hi)s; border-bottom: 2px solid %(border_hi)s; }
QPushButton { border-top: 2px solid %(border_hi)s; border-left: 2px solid %(border_hi)s;
  border-right: 4px solid %(shadow)s; border-bottom: 4px solid %(shadow)s; }
QPushButton:pressed { border-top: 4px solid %(shadow)s; border-left: 4px solid %(shadow)s;
  border-right: 2px solid %(border_hi)s; border-bottom: 2px solid %(border_hi)s; }
QPushButton:disabled { border-color: %(border)s; }
QWidget#home QPushButton#primary { border-right-width: 5px; border-bottom-width: 5px; }
QWidget#home QPushButton#primary:pressed { border-top-width: 5px; border-left-width: 5px; border-right-width: 2px; border-bottom-width: 2px; }
QComboBox { border-top: 2px solid %(border_hi)s; border-left: 2px solid %(border_hi)s; border-right: 3px solid %(shadow)s; border-bottom: 3px solid %(shadow)s; }
"""


def chrome_qss(theme_id: str, v1_borders: bool = False) -> str:
    t = load_theme(theme_id)
    def mix(a: str, b: str, k: float) -> str:
        ca, cb = QColor(a), QColor(b)
        return QColor(*(round(x + (y - x) * k) for x, y in
                        zip((ca.red(), ca.green(), ca.blue()), (cb.red(), cb.green(), cb.blue())))).name()
    extra = {"acc_hi": mix(t["accent"], "#ffffff", 0.28), "acc_lo": mix(t["accent"], "#000000", 0.22),
             "alt_hi": mix(t["bg_alt"], "#ffffff", 0.10), "bg_hi": mix(t["bg"], "#ffffff", 0.06)}
    return (_QSS if v1_borders else _QSS + _BEVEL) % {"accent_ink": t["bg"], **extra, **t}        # text on accent colour defaults to the background

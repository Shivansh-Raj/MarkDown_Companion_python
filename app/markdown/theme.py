"""Themes: assets/themes/<id>.json holds a colour palette; CSS and Qt stylesheet are generated from it.

Adding a theme = dropping a new JSON file next to retro.json.
"""
from __future__ import annotations

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
QWidget#root { background: %(shadow)s; }
QWidget#frame { background: %(bg)s; border: 2px solid %(border_hi)s; }
QWidget#titlebar { background: %(bg_alt)s; border-bottom: 2px solid %(border)s; }
QLabel#title { color: %(accent)s; font-family: Consolas, Menlo, monospace; font-weight: bold; font-size: 12px; }
QLabel#status { background: #5a2b2b; color: #ffd9d9; padding: 3px 8px; font-size: 11px; border-bottom: 2px solid #8a3b3b; }
QLabel#status[kind="info"] { background: %(bg_alt)s; color: %(accent2)s; border-bottom: 2px solid %(border)s; }
QToolButton#tbtn { color: %(fg)s; background: %(bg)s; border: 2px solid %(border)s; min-width: 18px; max-width: 18px;
  min-height: 16px; max-height: 16px; font-family: Consolas, Menlo, monospace; font-weight: bold; font-size: 11px; padding: 0; }
QToolButton#tbtn:hover { border-color: %(accent)s; color: %(accent)s; }
QToolButton#tbtn:pressed { background: %(border)s; }
QToolButton#tbtn[role="close"]:hover { background: #8a3b3b; color: white; border-color: #ff8a8a; }
QDialog, QMenu { background: %(bg)s; color: %(fg)s; font-family: Consolas, Menlo, monospace; }
QMenu { border: 2px solid %(border_hi)s; } QMenu::item { padding: 4px 22px; }
QMenu::item:selected { background: %(accent)s; color: %(bg)s; } QMenu::separator { height: 2px; background: %(border)s; }
QDialog QLabel, QCheckBox { color: %(fg)s; }
QLineEdit, QKeySequenceEdit, QComboBox { background: %(code_bg)s; color: %(fg)s; border: 2px solid %(border)s; padding: 3px; }
QPushButton { background: %(bg_alt)s; color: %(fg)s; border: 2px solid %(border_hi)s; padding: 4px 12px; }
QPushButton:hover { border-color: %(accent)s; color: %(accent)s; }
QSlider::groove:horizontal { height: 6px; background: %(border)s; } QSlider::handle:horizontal { background: %(accent)s; width: 10px; margin: -5px 0; }
"""


def chrome_qss(theme_id: str) -> str:
    return _QSS % load_theme(theme_id)

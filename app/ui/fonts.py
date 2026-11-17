"""One look for text across the app, the same as the Markdown view: a monospace body with pixel (Silkscreen) headings.

The Markdown pages get this from CSS. Qt widgets cannot read web-font files, so the Silkscreen font ships a second time as a
plain TrueType file (unwrapped from the same WOFF, see tools/woff_to_ttf.py) and is registered with Qt at start-up.
The home screen keeps its own font on purpose and does not use these helpers.
"""
from __future__ import annotations

from PySide6.QtCore import QByteArray
from PySide6.QtGui import QFont, QFontDatabase

from ..utils.paths import resource_dir

PIXEL = "Silkscreen"
BODY_FAMILIES = ["Cascadia Mono", "Consolas", "Menlo", "DejaVu Sans Mono", "monospace"]   # same stack as the Markdown view's CSS
_loaded: bool | None = None


def load_fonts() -> bool:
    """Register the bundled pixel font with Qt (once). Returns False if it could not be read: text then falls back gracefully."""
    global _loaded
    if _loaded is None:
        ok = False
        for weight in (400, 700):
            try:
                data = (resource_dir() / "fonts" / f"silkscreen-latin-{weight}-normal.ttf").read_bytes()
            except OSError:
                continue
            ok = QFontDatabase.addApplicationFontFromData(QByteArray(data)) >= 0 or ok
        _loaded = ok
    return _loaded


def body_font(px: int = 12, bold: bool = False) -> QFont:
    f = QFont()
    f.setFamilies(BODY_FAMILIES)
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setPixelSize(px)
    f.setBold(bold)
    return f


def pixel_font(px: int = 11) -> QFont:
    load_fonts()
    f = QFont(PIXEL)
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setPixelSize(px)
    return f

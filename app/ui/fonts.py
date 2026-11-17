"""One look for text across the app, the same as the Markdown view: a monospace body with pixel (Silkscreen) headings.

The Markdown pages get this from CSS. Qt widgets cannot read web-font files, so the Silkscreen font ships a second time as a
plain TrueType file (unwrapped from the same WOFF, see tools/woff_to_ttf.py) and is registered with Qt at start-up.
The home screen keeps its own font on purpose and does not use these helpers.
"""
from __future__ import annotations

import weakref

from PySide6.QtCore import QByteArray
from PySide6.QtGui import QFont, QFontDatabase

from ..utils.paths import resource_dir
from . import scale

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
    f.setPixelSize(scale.px(px))
    f.setBold(bold)
    return f


def pixel_font(px: int = 11) -> QFont:
    load_fonts()
    f = QFont(PIXEL)
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setPixelSize(scale.px(px))
    return f


# Widgets whose font was set by hand are remembered so a change of the text-size setting can re-apply it.
_tracked: list[tuple["weakref.ref", str, int, bool]] = []


def use_body_font(widget, px: int = 12, bold: bool = False) -> None:
    widget.setFont(body_font(px, bold))
    _tracked.append((weakref.ref(widget), "body", px, bold))


def use_pixel_font(widget, px: int = 11) -> None:
    widget.setFont(pixel_font(px))
    _tracked.append((weakref.ref(widget), "pixel", px, False))


def rescale_all() -> None:
    """Re-apply every tracked font at the current scale (and forget widgets that no longer exist)."""
    alive = []
    for ref, kind, px, bold in _tracked:
        w = ref()
        if w is None:
            continue
        try:
            w.setFont(body_font(px, bold) if kind == "body" else pixel_font(px))
        except RuntimeError:                          # the Qt object was deleted underneath its Python wrapper
            continue
        alive.append((ref, kind, px, bold))
    _tracked[:] = alive

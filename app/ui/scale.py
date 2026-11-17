"""One text-size setting for the whole interface.

The "Text size" percentage (Settings) used to zoom only the Markdown view. It now drives everything: the application font, every
font size in the style sheet, the fixed sizes of the title-bar buttons and list rows, and the shared body/pixel fonts.
Code that sets a size by hand goes through px() so it follows the setting.
"""
from __future__ import annotations

import re

_scale = 1.0
_base_font = None
_SIZE = re.compile(r"font-size:\s*(\d+(?:\.\d+)?)(px|pt)")


def get() -> float:
    return _scale


def set_percent(percent: int | float) -> None:
    global _scale
    _scale = max(0.5, min(3.0, float(percent) / 100.0))


def px(n: float) -> int:
    """A size in pixels at the current scale (never below 1)."""
    return max(1, round(n * _scale))


def scale_sizes(css: str) -> str:
    """Multiply every `font-size: Npx|Npt` in a style sheet (Qt QSS or the rich-text CSS) by the current scale."""
    if _scale == 1.0:
        return css
    return _SIZE.sub(lambda m: f"font-size: {round(float(m.group(1)) * _scale * 10) / 10:g}{m.group(2)}", css)


def base_font():
    """The platform's own default font, captured once before any scaling so 100% always means "as the system has it"."""
    global _base_font
    if _base_font is None:
        from PySide6.QtGui import QFont
        from PySide6.QtWidgets import QApplication
        _base_font = QFont(QApplication.font())
    return _base_font


def scaled_app_font():
    from PySide6.QtGui import QFont
    f = QFont(base_font())
    if f.pointSizeF() > 0:
        f.setPointSizeF(f.pointSizeF() * _scale)
    elif f.pixelSize() > 0:
        f.setPixelSize(px(f.pixelSize()))
    return f

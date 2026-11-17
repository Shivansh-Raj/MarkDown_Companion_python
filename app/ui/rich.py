"""Markdown in Qt text widgets, styled like the Markdown view: monospace body, pixel headings, themed colours."""
from __future__ import annotations

import weakref

from PySide6.QtWidgets import QTextBrowser

from ..markdown.renderer import render_markdown
from ..markdown.theme import load_theme
from . import scale
from .fonts import BODY_FAMILIES, PIXEL, body_font, load_fonts

_FAMILY = ", ".join(f'"{f}"' if " " in f else f for f in BODY_FAMILIES)


def default_stylesheet(theme_id: str) -> str:
    t = load_theme(theme_id)
    pixel = f'font-family: "{PIXEL}"; font-weight: bold;'        # headings use points: Qt ignores px sizes on heading tags
    css = (f"body {{ font-family: {_FAMILY}; }} a {{ color: {t['link']}; }}"
            f" h1 {{ {pixel} font-size: 14pt; color: {t['accent']}; }} h2 {{ {pixel} font-size: 12pt; color: {t['accent2']}; }}"
            f" h3 {{ {pixel} font-size: 11pt; color: {t['fg']}; }} h4, h5, h6 {{ {pixel} font-size: 10pt; color: {t['muted']}; }}"
            f" strong, b {{ color: {t['accent']}; }} em, i {{ color: {t['accent2']}; }} del {{ color: {t['muted']}; }}"
            f" code, pre {{ color: {t['accent2']}; background-color: {t['code_bg']}; font-family: {_FAMILY}; }}"
            f" blockquote {{ color: {t['muted']}; }} th {{ color: {t['accent']}; }}")
    return scale.scale_sizes(css)


_browsers: list[tuple["weakref.ref", str, int]] = []


def style_browser(browser: QTextBrowser, theme_id: str, px: int = 12) -> None:
    """Give a text browser the Markdown view's fonts and colours (call once after creating it)."""
    load_fonts()
    browser.setFont(body_font(px))
    browser.document().setDefaultFont(body_font(px))
    browser.document().setDefaultStyleSheet(default_stylesheet(theme_id))
    _browsers.append((weakref.ref(browser), theme_id, px))


def rescale_browsers() -> None:
    """Text-size setting changed: re-font open browsers. Heading sizes inside already-rendered text refresh on the next render."""
    alive = []
    for ref, theme_id, px in _browsers:
        b = ref()
        if b is None:
            continue
        try:
            b.setFont(body_font(px))
            b.document().setDefaultFont(body_font(px))
            b.document().setDefaultStyleSheet(default_stylesheet(theme_id))
        except RuntimeError:
            continue
        alive.append((ref, theme_id, px))
    _browsers[:] = alive


def set_markdown(browser: QTextBrowser, md: str) -> None:
    """Render Markdown with the app's own renderer, so headings, bold and code look the same as in a note window."""
    bar = browser.verticalScrollBar()
    pos, at_end = bar.value(), bar.value() >= bar.maximum() - 2
    browser.setHtml(render_markdown(md) if md.strip() else "")
    bar.setValue(bar.maximum() if at_end else pos)

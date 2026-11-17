"""Markdown in Qt text widgets, styled like the Markdown view: monospace body, pixel headings, themed colours."""
from __future__ import annotations

from PySide6.QtWidgets import QTextBrowser

from ..markdown.renderer import render_markdown
from ..markdown.theme import load_theme
from .fonts import BODY_FAMILIES, PIXEL, body_font, load_fonts

_FAMILY = ", ".join(f'"{f}"' if " " in f else f for f in BODY_FAMILIES)


def default_stylesheet(theme_id: str) -> str:
    t = load_theme(theme_id)
    pixel = f'font-family: "{PIXEL}"; font-weight: bold;'        # headings use points: Qt ignores px sizes on heading tags
    return (f"body {{ font-family: {_FAMILY}; }} a {{ color: {t['link']}; }}"
            f" h1 {{ {pixel} font-size: 14pt; color: {t['accent']}; }} h2 {{ {pixel} font-size: 12pt; color: {t['accent2']}; }}"
            f" h3 {{ {pixel} font-size: 11pt; color: {t['fg']}; }} h4, h5, h6 {{ {pixel} font-size: 10pt; color: {t['muted']}; }}"
            f" strong, b {{ color: {t['accent']}; }} em, i {{ color: {t['accent2']}; }} del {{ color: {t['muted']}; }}"
            f" code, pre {{ color: {t['accent2']}; background-color: {t['code_bg']}; font-family: {_FAMILY}; }}"
            f" blockquote {{ color: {t['muted']}; }} th {{ color: {t['accent']}; }}")


def style_browser(browser: QTextBrowser, theme_id: str, px: int = 12) -> None:
    """Give a text browser the Markdown view's fonts and colours (call once after creating it)."""
    load_fonts()
    browser.setFont(body_font(px))
    browser.document().setDefaultFont(body_font(px))
    browser.document().setDefaultStyleSheet(default_stylesheet(theme_id))


def set_markdown(browser: QTextBrowser, md: str) -> None:
    """Render Markdown with the app's own renderer, so headings, bold and code look the same as in a note window."""
    bar = browser.verticalScrollBar()
    pos, at_end = bar.value(), bar.value() >= bar.maximum() - 2
    browser.setHtml(render_markdown(md) if md.strip() else "")
    bar.setValue(bar.maximum() if at_end else pos)

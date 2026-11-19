"""Consistent text across windows (monospace body, Silkscreen headings, like the Markdown view); the home screen is left alone."""
import pytest
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QFormLayout, QLabel, QTextBrowser

from app.ai import ask
from app.ui import fonts
from app.ui.ask_panel import conversation_html
from app.ui.rich import default_stylesheet, set_markdown, style_browser
from app.utils.paths import resource_dir


def test_the_pixel_font_ships_as_a_file_qt_can_load(qapp):
    assert fonts.load_fonts() is True
    assert fonts.PIXEL in QFontDatabase.families()
    assert fonts.pixel_font(11).family() == fonts.PIXEL


def test_the_body_stack_is_the_same_one_the_markdown_pages_use():
    css = (resource_dir() / "themes" / "base.css").read_text("utf-8")
    body_rule = next(line for line in css.splitlines() if line.startswith("body {"))
    for family in fonts.BODY_FAMILIES[:-1]:
        assert family in body_rule
    assert fonts.body_font(12).families()[0] == "Cascadia Mono" and fonts.body_font(12).pixelSize() == 12


def test_rich_text_headings_use_the_pixel_font_and_bold_uses_the_accent(qapp):
    b = QTextBrowser()
    style_browser(b, "retro")
    set_markdown(b, "# Title\n\nSome **bold** text and `code`")
    html = b.toHtml()
    assert "Silkscreen" in html and "Title" in b.toPlainText()
    assert "font-size:14pt" in html and "xx-large" not in html           # heading size comes from our stylesheet, not Qt's default
    css = default_stylesheet("retro")
    assert "h1 {" in css and "Silkscreen" in css and "strong" in css


def test_the_ask_view_has_pixel_tags_checked_quotes_and_no_invented_ones():
    turns = [ask.Turn("What is left?", "- **Book the venue**\n- Order the cake", [("Book the venue for March 14", True), ("made up", False)]),
             ask.Turn("Who pays?", "Priya pays.", [("Priya pays", False)]),
             ask.Turn("Anything?", "Groq request failed", error=True)]
    html = conversation_html(turns, "retro", "And then?", "Streaming…")
    assert html.count("YOU") == 4 and "FILE SAYS" in html and "PROBLEM" in html
    assert "Silkscreen" in html and "Quotes from the file" in html
    assert "Book the venue for March 14" in html and "made up" not in html
    assert "No exact quote could be matched" in html and "Streaming" in html
    assert "Reading the file" in conversation_html([], "retro", "q", "")


def test_panels_and_dialogs_use_the_body_font_but_the_home_screen_keeps_its_own(make_app, md):
    app = make_app()
    w = app.open_path(str(md()))
    app.show_settings()
    app.show_voice()
    app.show_ai_create()
    for widget in (w.ask_panel, w.ai_panel, app._settings_win, app._voice_win, app._ai_win):
        assert widget.font().families()[0] == "Cascadia Mono", type(widget).__name__
    # the home screen was deliberately left as it was: it never receives the shared body font
    assert app.home.font().families() != fonts.BODY_FAMILIES and app.home.font().family() == app.qapp.font().family()


def test_settings_row_labels_are_in_the_pixel_font(make_app):
    app = make_app()
    app.show_settings()
    s = app._settings_win
    form = s._content.layout()                           # the form sits inside a scroll area so a tall settings window never leaves the screen
    assert isinstance(form, QFormLayout)
    labels = [form.itemAt(i, QFormLayout.ItemRole.LabelRole).widget() for i in range(form.rowCount())
              if form.itemAt(i, QFormLayout.ItemRole.LabelRole) and form.itemAt(i, QFormLayout.ItemRole.LabelRole).widget()]
    named = [lbl for lbl in labels if isinstance(lbl, QLabel) and lbl.text()]
    assert named and all(lbl.font().family() == fonts.PIXEL for lbl in named)

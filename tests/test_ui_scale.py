"""The Text size setting scales the whole interface (buttons, labels, lists, title bars), not only the Markdown text."""
import re

import pytest

from app.markdown.theme import chrome_qss
from app.ui import fonts, scale
from app.ui import title_bar as tb


def sizes(css):
    return [float(m.group(1)) for m in re.finditer(r"font-size:\s*([\d.]+)px", css)]


def test_the_scale_helpers_multiply_sizes_and_never_go_below_one_pixel():
    scale.set_percent(150)
    assert scale.px(20) == 30 and scale.px(0.2) == 1 and scale.get() == 1.5
    assert scale.scale_sizes("a { font-size: 12px; } b { font-size: 11pt; padding: 4px; }") == "a { font-size: 18px; } b { font-size: 16.5pt; padding: 4px; }"
    scale.set_percent(100)
    css = "a { font-size: 12px; }"
    assert scale.scale_sizes(css) == css and scale.px(13) == 13
    scale.set_percent(10000)
    assert scale.get() == 3.0                                    # sane upper bound


def test_every_font_size_in_the_style_sheet_follows_the_setting():
    base = sizes(chrome_qss("retro"))
    scale.set_percent(150)
    big = sizes(chrome_qss("retro"))
    scale.set_percent(70)
    small = sizes(chrome_qss("retro"))
    assert len(base) == len(big) == len(small) > 8
    assert all(abs(b - a * 1.5) <= 0.1 for a, b in zip(base, big)) and all(abs(s - a * 0.7) <= 0.1 for a, s in zip(base, small))


def test_hand_set_fonts_scale_and_pixel_font_too(qapp):
    assert fonts.body_font(12).pixelSize() == 12 and fonts.pixel_font(11).pixelSize() == 11
    scale.set_percent(200)
    assert fonts.body_font(12).pixelSize() == 24 and fonts.pixel_font(11).pixelSize() == 22


def test_title_bar_buttons_grow_with_the_text_so_labels_are_not_clipped(qapp):
    bar = tb.TitleBar("x")
    wide = bar.ai_btn
    before = (bar.height(), wide.width(), wide.height())
    scale.set_percent(150)
    bar.rescale()
    assert bar.height() == tb.height() == round(before[0] * 1.5) and wide.width() == round(before[1] * 1.5) and wide.height() == round(before[2] * 1.5)
    scale.set_percent(100)
    bar.rescale()
    assert (bar.height(), wide.width(), wide.height()) == before


def test_changing_the_setting_rescales_a_running_app_live(make_app, md):
    app = make_app()
    app.start(show_home=False)
    w = app.open_path(str(md()))
    app.show_settings()
    app.show_voice()
    base_pt = app.qapp.font().pointSizeF()
    base_h = w.title_bar.height()
    base_row = app.home.results.itemDelegate().sizeHint(type("O", (), {"rect": app.home.results.rect()})(), None).height()
    base_ask = w.ask_panel.font().pixelSize()
    base_voice = app._voice_win.font().pixelSize()
    base_settings = app._settings_win.font().pixelSize()

    app.settings.set("font_scale", 150)                          # what pressing Apply in Settings does
    assert scale.get() == 1.5
    assert app.qapp.font().pointSizeF() == pytest.approx(base_pt * 1.5)                   # every plain widget: buttons, labels, inputs
    assert w.title_bar.height() == round(base_h * 1.5) and w.minimumHeight() >= round(30 * 1.5)
    assert app.home.results.itemDelegate().sizeHint(type("O", (), {"rect": app.home.results.rect()})(), None).height() == round(base_row * 1.5)
    assert w.ask_panel.font().pixelSize() == round(base_ask * 1.5) and app._voice_win.font().pixelSize() == round(base_voice * 1.5)
    assert w.view.font().pointSizeF() == pytest.approx(base_pt * 1.5)                        # the note text itself zooms once, not twice
    assert max(sizes(app.qapp.styleSheet())) >= 22 * 1.5 - 0.1                               # the 22px window titles became 33px
    assert app._settings_win.font().pixelSize() == round(base_settings * 1.5)

    app.settings.set("font_scale", 100)                          # and back
    assert scale.get() == 1.0 and app.qapp.font().pointSizeF() == pytest.approx(base_pt)
    assert w.title_bar.height() == base_h and w.ask_panel.font().pixelSize() == base_ask


def test_the_note_text_is_not_scaled_twice_when_a_window_opens_at_a_large_size(make_app, md):
    first = make_app()
    first.settings.set("font_scale", 150)
    app = make_app()                                              # the app font is already 150% when this window is created
    w = app.open_path(str(md()))
    assert w.view.font().pointSizeF() == pytest.approx(scale.base_font().pointSizeF() * 1.5)


def test_a_saved_scale_is_applied_when_the_app_starts(make_app):
    first = make_app()
    first.settings.set("font_scale", 120)
    second = make_app()                                          # same data dir: it reads the saved value at start-up
    assert scale.get() == 1.2 and second.windows.ai.settings.get("font_scale") == 120
    second.start(show_home=False)
    assert max(sizes(second.qapp.styleSheet())) >= 22 * 1.2 - 0.1


def test_a_rolled_up_note_window_stays_tall_enough_for_its_title_bar_after_rescaling(make_app, md):
    app = make_app()
    w = app.open_path(str(md()))
    w.set_compact(True)
    app.settings.set("font_scale", 200)
    assert w.height() == w._compact_height() == w.title_bar.height() + 2 * 6 + 4          # EDGE = 6
    w.set_compact(False)
    assert w.minimumHeight() == w._compact_height()


def test_the_home_screens_font_family_is_untouched_by_scaling(make_app):
    app = make_app()
    family = app.home.font().family()
    app.settings.set("font_scale", 150)
    assert app.home.font().family() == family == app.qapp.font().family()               # only the size changes, never the face


def test_the_settings_window_grows_so_larger_text_is_not_cut_off(make_app, qapp):
    from tests.conftest import wait_for
    app = make_app()
    app.show_settings()
    win = app._settings_win
    win.show()
    before = win.width()
    app.settings.set("font_scale", 150)
    assert wait_for(lambda: win.width() > before * 1.2)                       # the dialog resized itself to its larger content
    assert win.width() >= win.sizeHint().width() and win.minimumWidth() >= scale.px(500) - 1
    app.settings.set("font_scale", 100)
    assert wait_for(lambda: win.minimumWidth() < 540)                        # the minimum shrinks back (the window itself stays as the user left it)

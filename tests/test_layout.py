"""Buttons must never overlap or be squeezed, however small the window is made."""
from PySide6.QtWidgets import QPushButton, QToolButton, QWidget

from app.ui.flow_layout import FlowLayout
from app.ui.markdown_window import MIN_W


def _overlaps(widgets):
    rects = [w.geometry() for w in widgets]
    return any(a.intersects(b) for i, a in enumerate(rects) for b in rects[i + 1:])


def test_title_bar_buttons_fixed_size_and_never_overlap_at_minimum_width(make_app, md):
    app = make_app()
    win = app.open_path(str(md("a-rather-long-file-name-for-a-small-window.md")))
    win.resize(MIN_W, 300)
    win.show()
    win.grab()                                           # forces layout
    buttons = [b for b in win.title_bar.findChildren(QToolButton) if b.isVisible()]
    assert len(buttons) >= 6
    assert not _overlaps(buttons)
    assert all(b.width() == b.minimumWidth() == b.maximumWidth() for b in buttons)   # never squeezed
    assert max(b.geometry().right() for b in buttons) <= win.title_bar.width()
    assert win.title_bar.title.text() != "" and win.title_bar.title.toolTip()        # elided, full name in tooltip


def test_edit_bar_wraps_instead_of_overlapping_when_narrow(make_app, md):
    app = make_app()
    win = app.open_path(str(md("a.md")))
    win.resize(MIN_W, 300)
    win.show()
    win.edit_requested.emit(win.key)
    win.grab()
    buttons = [win.save_btn, win.cancel_btn, win.undo_ai_btn]
    assert not _overlaps(buttons)
    assert all(b.width() >= b.sizeHint().width() - 1 for b in buttons)               # full labels visible
    assert all(win.editbar.rect().contains(b.geometry()) for b in buttons)


def test_flow_layout_wraps_and_reports_height_for_width(qapp):
    host = QWidget()
    lay = FlowLayout(host, 0, 6, 4)
    for t in ("Alpha", "Bravo", "Charlie", "Delta"):
        lay.addWidget(QPushButton(t))
    wide, narrow = lay.heightForWidth(600), lay.heightForWidth(110)
    assert narrow > wide                                  # narrower -> more rows
    host.resize(110, narrow)
    host.show()
    lay.setGeometry(host.rect())
    rects = [lay.itemAt(i).geometry() for i in range(lay.count())]
    assert not any(a.intersects(b) for i, a in enumerate(rects) for b in rects[i + 1:])
    assert all(host.rect().contains(r) for r in rects)


def test_ai_panel_form_scrolls_in_a_short_window(make_app, md):
    app = make_app()
    win = app.open_path(str(md("a.md")))
    win.resize(MIN_W, 200)
    win.show()
    win.open_ai()
    win.grab()
    scroll = win.ai_panel.stack.widget(0)
    assert scroll.verticalScrollBar().maximum() > 0       # content taller than the window scrolls

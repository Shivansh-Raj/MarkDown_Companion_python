"""V1-style home screen: search, one list with badges, keyboard control, footer buttons, shortcut banner."""
import os

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

KEY, OPEN, MISSING = (Qt.ItemDataRole.UserRole + n for n in (1, 2, 3))


@pytest.fixture
def home(make_app, md):
    app = make_app()
    paths = [md(n, f"# {n}\n- [ ] x\n") for n in ("alpha plan.md", "beta notes.md", "gamma tasks.md")]
    for p in paths:
        app.open_path(str(p))
    app.windows.close_window(app.windows.windows()[0].key)           # gamma... one stays closed
    app.home.show()
    app.home.refresh()
    return app, app.home, paths


def names(h):
    return [h.results.item(i).text() for i in range(h.results.count())]


def test_one_list_newest_first_with_open_and_missing_markers(home):
    app, h, paths = home
    assert names(h) == ["gamma tasks.md", "beta notes.md", "alpha plan.md"]
    opened = {h.results.item(i).text(): h.results.item(i).data(OPEN) for i in range(3)}
    assert sum(opened.values()) == 2
    paths[1].unlink()
    h.refresh()
    assert [h.results.item(i).data(MISSING) for i in range(3)].count(True) == 1


def test_search_filters_by_name_or_folder_and_says_when_nothing_matches(home):
    app, h, paths = home
    h.search.setText("BETA")
    assert names(h) == ["beta notes.md"]
    h.search.setText(os.path.basename(os.path.dirname(paths[0])))        # the folder name matches every file
    assert len(names(h)) == 3
    h.search.setText("zzz")
    assert names(h) == ["No matches."] and not h.results.item(0).flags() & Qt.ItemFlag.ItemIsEnabled


def test_keyboard_arrows_move_enter_opens_and_escape_hides(home):
    app, h, paths = home
    opened = []
    h.open_path_requested.connect(opened.append)
    assert h.results.currentRow() == 0
    QTest.keyClick(h.search, Qt.Key.Key_Down)
    QTest.keyClick(h.search, Qt.Key.Key_Down)
    QTest.keyClick(h.search, Qt.Key.Key_Down)                              # clamps at the last row
    assert h.results.currentRow() == 2
    QTest.keyClick(h.search, Qt.Key.Key_Up)
    assert h.results.currentRow() == 1
    QTest.keyClick(h.search, Qt.Key.Key_Return)
    assert opened == [h.results.item(1).data(Qt.ItemDataRole.UserRole)]
    QTest.keyClick(h.search, Qt.Key.Key_Escape)
    assert not h.isVisible()


def test_typing_keeps_working_in_the_search_box(home):
    app, h, paths = home
    QTest.keyClicks(h.search, "gam")
    assert names(h) == ["gamma tasks.md"] and h.search.text() == "gam"


def test_footer_buttons_and_gear_emit_their_signals(home, monkeypatch):
    app, h, paths = home
    # the app's own handlers would open a modal file picker / extra windows: replace them so only the signals are checked
    for sig in ("open_dialog_requested", "ai_create_requested", "voice_requested", "settings_requested"):
        getattr(h, sig).disconnect()
    fired = []
    for sig in ("open_dialog_requested", "ai_create_requested", "voice_requested", "settings_requested", "exit_requested"):
        getattr(h, sig).connect(lambda s=sig: fired.append(s))
    h.open_btn.click()
    h.ai_btn.click()
    h.voice_btn.click()
    h.title_bar.settings_clicked.emit()
    assert fired == ["open_dialog_requested", "ai_create_requested", "voice_requested", "settings_requested"]
    assert h.open_btn.text() == "+ ADD FILE" and h.ai_btn.text().endswith("NEW WITH AI") and h.voice_btn.text().endswith("VOICE")
    assert h.open_btn.property("primary") is True and not h.ai_btn.property("primary")


def test_a_failed_shortcut_shows_a_banner_and_clearing_it_hides_it(home):
    app, h, paths = home
    assert not h.status.isVisibleTo(h)
    h.set_shortcut_status("Ctrl+Shift+M is already in use.")
    assert h.status.isVisibleTo(h) and "already in use" in h.status.text()
    h.set_shortcut_status("")
    assert not h.status.isVisibleTo(h)
    assert "select" in h.hint.text() and "Esc close" in h.hint.text()


def test_the_cat_sits_on_the_add_file_button_and_theme_changes_are_applied(home):
    app, h, paths = home
    h.resize(540, 520)
    h._place_cat()
    cat, btn = h.cat.geometry(), h.open_btn.geometry()
    assert btn.left() <= cat.left() and cat.right() <= btn.right() + 1 and cat.bottom() <= btn.top() + 8     # perched on top of the button
    app.settings.set("theme", "amber")
    h.apply_theme()                                                         # must not raise and keeps the shadows
    assert all(b.graphicsEffect() is not None for b in (h.open_btn, h.ai_btn, h.voice_btn))


def test_drag_and_drop_of_markdown_still_opens_files(home, md):
    from PySide6.QtCore import QMimeData, QUrl, QPointF
    from PySide6.QtGui import QDropEvent
    app, h, paths = home
    seen = []
    h.open_path_requested.connect(seen.append)
    p = md("dropped.md")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(p)), QUrl.fromLocalFile(str(p.with_suffix(".png")))])
    h.dropEvent(QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    assert seen == [str(p)]                                                 # the non-Markdown file is ignored

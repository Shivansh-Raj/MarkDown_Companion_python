import os

from PySide6.QtCore import Qt
from conftest import FakeBackend, wait_for
from app.core.shortcut_manager import MOD_CONTROL, MOD_SHIFT, ShortcutManager, parse_shortcut
from app.services.persistence import Database
from app.utils.geometry import clamp_to_screens


def test_multiple_independent_windows(make_app, md):
    app = make_app()
    a, b = md("a.md", "# A\n- [ ] x\n"), md("b.md", "# B\n- [ ] y\n")
    wa, wb = app.open_path(str(a)), app.open_path(str(b))
    assert wa is not wb and len(app.windows.windows()) == 2
    wa.move(10, 10); wb.move(500, 300); wa.resize(300, 300); wb.resize(450, 500)
    assert wa.geometry().topLeft() != wb.geometry().topLeft()
    wa.set_compact(True)
    assert wa.compact and not wb.compact                 # modes are independent
    # toggling in A must never touch B
    app.files.toggle_checkbox(wa.key, 1, False)
    assert a.read_text() == "# A\n- [x] x\n" and b.read_text() == "# B\n- [ ] y\n"
    app.windows.close_window(wa.key)
    assert len(app.windows.windows()) == 1 and wb.isVisible()   # closing one leaves the other


def test_duplicate_open_focuses_existing_window(make_app, md, tmp_path):
    app = make_app()
    p = md("dup.md")
    w1 = app.open_path(str(p))
    w2 = app.open_path(str(p))
    alias = tmp_path / "docs" / ".." / "docs" / "dup.md"        # same file, different spelling
    w3 = app.open_path(str(alias))
    assert w1 is w2 is w3 and len(app.windows.windows()) == 1


def test_window_state_persists_across_sessions(make_app, md, qapp, data_dir):
    p = md("persist.md")
    app = make_app()
    w = app.open_path(str(p))
    w.setGeometry(120, 140, 333, 444)
    w.set_opacity(0.7)
    key = w.key
    app.windows.save_all()
    app.shutdown()

    db = Database(data_dir / "companion.db")
    row = db.get_file(key)
    assert (row["x"], row["y"], row["w"], row["h"]) == (120, 140, 333, 444)
    assert abs(row["opacity"] - 0.7) < 1e-6 and row["is_open"] == 1   # still flagged for restore
    db.close()

    app2 = make_app()
    w2 = app2.open_path(str(p))
    g = w2.geometry()
    assert (g.x(), g.y(), g.width(), g.height()) == (120, 140, 333, 444)
    assert abs(w2.opacity - 0.7) < 1e-6


def test_user_close_clears_restore_flag_but_keeps_geometry(make_app, md, data_dir):
    p = md("c.md")
    app = make_app()
    w = app.open_path(str(p))
    key = w.key
    app.windows.close_window(key)
    row = app.db.get_file(key)
    assert row["is_open"] == 0 and row["w"] is not None


def test_restore_windows_setting(make_app, md):
    p = md("r.md")
    app = make_app()
    app.open_path(str(p))
    app.settings.set("restore_windows", True)
    app.shutdown()
    app2 = make_app()
    assert app2.windows.windows() == []        # nothing opens until start()
    app2.start()
    assert len(app2.windows.windows()) == 1


def test_recent_files_and_registry(make_app, md):
    app = make_app()
    for n in ("1.md", "2.md", "3.md"):
        app.open_path(str(md(n)))
    names = [os.path.basename(r["path"]) for r in app.db.recent_files(10)]
    assert names == ["3.md", "2.md", "1.md"]


def test_geometry_clamp_multimonitor():
    screens = [(0, 0, 1920, 1080), (1920, 0, 1280, 1024)]
    assert clamp_to_screens((100, 100, 400, 500), screens) == (100, 100, 400, 500)
    assert clamp_to_screens((2000, 50, 400, 500), screens) == (2000, 50, 400, 500)   # on 2nd monitor
    x, y, w, h = clamp_to_screens((2000, 50, 400, 500), screens[:1])                 # monitor unplugged
    assert 0 <= x <= 1920 - w and 0 <= y
    x, y, w, h = clamp_to_screens((0, 0, 5000, 5000), screens[:1])                   # resolution dropped
    assert w <= 1920 and h <= 1080


# ---- shortcuts -------------------------------------------------------------------
def test_parse_shortcut():
    assert parse_shortcut("Ctrl+Shift+M") == (MOD_CONTROL | MOD_SHIFT, ord("M"))
    assert parse_shortcut("Ctrl+Alt+F5")[1] == 0x74
    for bad in ("M", "Shift+M", "Ctrl+Nope", "Foo+M"):
        try:
            parse_shortcut(bad)
        except Exception:
            continue
        raise AssertionError(bad)


def test_shortcut_registration_conflict_and_cleanup(qapp):
    be = FakeBackend(taken={(MOD_CONTROL | MOD_SHIFT, ord("M"))})
    sm = ShortcutManager(be)
    res = sm.register("primary", "Ctrl+Shift+M")
    assert not res.ok and "already in use" in res.message and be.registered == {}
    assert sm.register("primary", "Ctrl+Shift+K").ok and len(be.registered) == 1
    fired = []
    sm.activated.connect(fired.append)
    sm.dispatch(list(be.registered)[0])
    assert fired == ["primary"]
    assert sm.register("primary", "Ctrl+Shift+J").ok and len(be.registered) == 1   # rebind replaces
    sm.unregister_all()
    assert be.registered == {} and be.closed


def test_unsupported_platform_degrades_gracefully(qapp):
    from app.core.shortcut_manager import UnsupportedBackend
    res = ShortcutManager(UnsupportedBackend()).register("p", "Ctrl+Shift+M")
    assert not res.ok and res.message


# ---- startup / shutdown -----------------------------------------------------------
def test_startup_registers_shortcut_and_opens_nothing(make_app):
    be = FakeBackend()
    app = make_app(be)
    app.start()
    assert len(be.registered) == 2 and app.windows.windows() == []   # main + home shortcuts


def test_startup_survives_shortcut_conflict(make_app):
    be = FakeBackend(taken={(MOD_CONTROL | MOD_SHIFT, ord("M"))})
    app = make_app(be)
    app.start()                                   # must not raise
    assert "already in use" in app.shortcut_status


def test_shutdown_cleans_up(make_app, md):
    be = FakeBackend()
    app = make_app(be)
    app.start()
    app.open_path(str(md()))
    app.shutdown()
    assert be.registered == {} and be.closed and app.windows.windows() == []
    app.shutdown()                                # idempotent


def test_shortcut_flow_picker_then_focus(make_app, md, monkeypatch):
    app = make_app()
    p = md("assoc.md")
    calls = []
    monkeypatch.setattr("app.core.application.pick_markdown_file", lambda d: (calls.append(1), str(p))[1])
    app._on_shortcut("primary")                    # first time: picker + remembered
    assert calls == [1] and app.settings.get("associated_file") == str(p)
    app._on_shortcut("primary")                    # afterwards: no picker, same window
    assert calls == [1] and len(app.windows.windows()) == 1


def test_single_instance_lock(make_app, qapp, data_dir):
    from app.core.application import CompanionApp
    a = make_app()
    assert a.acquire_single_instance()
    b = CompanionApp(qapp, backend=FakeBackend(), with_tray=False, poll=False)
    assert not b.acquire_single_instance()
    b.shutdown()


def test_ui_updates_after_external_edit(make_app, md):
    app = make_app()
    p = md("live.md", "# one\n")
    w = app.open_path(str(p))
    p.write_text("# two\n")
    assert wait_for(lambda: "two" in w.view.toPlainText())
    assert w.isVisible()


def test_deleted_file_shows_banner_and_keeps_content(make_app, md):
    app = make_app()
    p = md("gone.md", "# keep me\n")
    w = app.open_path(str(p))
    p.unlink()
    assert wait_for(lambda: w.status.text() != "")
    assert "keep me" in w.view.toPlainText()


# ---- home screen --------------------------------------------------------------------
def test_home_shows_on_startup_without_opening_picker(make_app, monkeypatch):
    app = make_app()
    monkeypatch.setattr("app.core.application.pick_markdown_file", lambda d: (_ for _ in ()).throw(AssertionError("picker")))
    app.start()
    assert app.home.isVisible() and app.windows.windows() == []


def test_home_can_be_disabled_or_skipped(make_app):
    app = make_app()
    app.start(show_home=False)
    assert not app.home.isVisible()
    app2 = make_app()
    app2.settings.set("show_home", False)
    app2.start()
    assert not app2.home.isVisible()


def test_home_lists_recent_and_open_windows(make_app, md):
    app = make_app()
    p = md("home.md")
    w = app.open_path(str(p))
    app.home.refresh()
    item = app.home.results.item(0)
    assert item.text() == "home.md" and item.data(Qt.ItemDataRole.UserRole + 1) == w.key       # one list: files, newest first
    assert item.data(Qt.ItemDataRole.UserRole + 2) is True                                      # shown with the OPEN badge
    app.home.results.itemActivated.emit(item)                                                  # opens/focuses, no duplicate
    assert len(app.windows.windows()) == 1


def test_home_marks_missing_and_forgets(make_app, md):
    app = make_app()
    p = md("m.md")
    app.open_path(str(p))
    app.windows.close_window(app.windows.windows()[0].key)
    p.unlink()
    app.home.refresh()
    item = app.home.results.item(0)
    assert item.data(Qt.ItemDataRole.UserRole + 3) is True and item.data(Qt.ItemDataRole.UserRole + 2) is False   # MISSING badge, not open
    app.home.forget_requested.emit(item.data(Qt.ItemDataRole.UserRole + 1))
    app.home.refresh()
    assert "No files yet" in app.home.results.item(0).text()


def test_home_uses_same_chrome_as_note_windows(make_app, md):
    from PySide6.QtCore import Qt
    app = make_app()
    w = app.open_path(str(md()))
    for win in (app.home, w):
        f = win.windowFlags()
        assert f & Qt.WindowType.FramelessWindowHint and f & Qt.WindowType.WindowStaysOnTopHint
        assert win.findChild(type(w.title_bar), "titlebar") is not None


def test_home_reachable_after_closing_everything(make_app, md):
    app = make_app()
    app.start(show_home=False)
    w = app.open_path(str(md()))
    app.home.hide()
    app.windows.close_window(w.key)
    assert not app.home.isVisible()
    app._on_shortcut("home")                      # global shortcut
    assert app.home.isVisible()
    app.home.hide()
    w2 = app.open_path(str(md("again.md")))
    w2.title_bar.home_clicked.emit()              # title-bar home button
    assert app.home.isVisible()


# ---- edit mode (window level) ------------------------------------------------------------
def _edit(app, w, text=None):
    w.title_bar.edit_btn.click()
    assert w.editing
    if text is not None:
        w.editor.setPlainText(text)


def test_edit_save_writes_file_and_rerenders(make_app, md):
    app = make_app()
    p = md("ed.md", "# Old\n")
    w = app.open_path(str(p))
    _edit(app, w, "# New heading\n\n- [ ] added\n")
    assert w.is_dirty() and w.editor.isVisibleTo(w) and not w.view.isVisibleTo(w)
    w.save_btn.click()
    assert not w.editing and p.read_text() == "# New heading\n\n- [ ] added\n"
    assert wait_for(lambda: "New heading" in w.view.toPlainText())


def test_edit_cancel_needs_second_press_when_dirty(make_app, md):
    app = make_app()
    p = md("c2.md", "keep\n")
    w = app.open_path(str(p))
    _edit(app, w)
    w.cancel_btn.click()                       # clean -> leaves immediately
    assert not w.editing
    _edit(app, w, "typed something\n")
    w.cancel_btn.click()
    assert w.editing and w.cancel_btn.text() == "DISCARD?"
    w.cancel_btn.click()
    assert not w.editing and p.read_text() == "keep\n"


def test_edit_external_change_conflict_keeps_draft(make_app, md):
    app = make_app()
    p = md("cf.md", "base\n")
    w = app.open_path(str(p))
    _edit(app, w, "my draft\n")
    p.write_text("external\n")
    wait_for(lambda: False, 0.4)
    assert w.editing and w.editor.toPlainText() == "my draft\n"      # editor undisturbed
    w.save_btn.click()
    assert w.editing and p.read_text() == "external\n"               # refused, text kept
    assert "changed on disk" in w.status.text()
    w.save_btn.click()                                               # deliberate overwrite
    assert not w.editing and p.read_text() == "my draft\n"


def test_edit_disabled_when_missing_or_not_utf8(make_app, md, tmp_path):
    app = make_app()
    p = md("m2.md", "x\n")
    w = app.open_path(str(p))
    p.unlink()
    assert wait_for(lambda: not w.title_bar.edit_btn.isEnabled())
    b = tmp_path / "bin.md"
    b.write_bytes(b"caf\xe9\n")
    w2 = app.open_path(str(b))
    w2.title_bar.edit_btn.click()
    assert not w2.editing and "UTF-8" in w2.status.text()


def test_close_with_unsaved_edit_needs_confirmation(make_app, md):
    app = make_app()
    w = app.open_path(str(md("cl.md", "z\n")))
    _edit(app, w, "unsaved\n")
    w.title_bar.close_clicked.emit()
    assert len(app.windows.windows()) == 1 and "Unsaved" in w.status.text()
    w.title_bar.close_clicked.emit()
    assert app.windows.windows() == []

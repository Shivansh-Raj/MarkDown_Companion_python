"""End-to-end AI flows with a fake Groq: no network, no real key."""
import pytest
from conftest import wait_for

DOC = "# Plan\n\nintro\n\n## A\n- [x] one\n- [ ] two\n\n## B\n- [ ] three\n"
EDITED_A = "## A\n- [ ] one\n- [ ] two\n- [ ] extra\n"


@pytest.fixture
def fake_groq(monkeypatch):
    """Replaces the network call. `script["output"]` is returned; `script["hook"]` runs mid-stream."""
    script = {"output": EDITED_A, "hook": None, "calls": []}

    def fake_stream(api_key, system, user, on_text, cancel=None):
        script["calls"].append((api_key, system, user))
        on_text(script["output"][:10])
        if script["hook"]:
            script["hook"]()
        on_text(script["output"])
        return script["output"]

    monkeypatch.setattr("app.ai.worker.stream_chat", fake_stream)
    return script


@pytest.fixture
def app(make_app):
    a = make_app()
    a.windows.ai.set_api_key("gsk_test")
    a.settings.set("animations", False)
    return a


def _run_edit(win, instruction="add an extra task", section=1):
    panel = win.ai_panel
    win.open_ai()
    assert win._ai_open
    panel.rb_sec.setChecked(True)
    panel.sec_box.setCurrentIndex(section)
    panel.instruction.setPlainText(instruction)
    panel.start()
    assert wait_for(lambda: panel.stack.currentIndex() == 2)
    return panel


def test_ai_edit_applies_to_draft_keeps_progress_and_saves(app, md, fake_groq):
    p = md("plan.md", DOC)
    win = app.open_path(str(p))
    panel = _run_edit(win)
    assert "Instruction:\nadd an extra task" in fake_groq["calls"][0][2]
    assert panel.apply_btn.isEnabled()
    panel.apply()
    assert win.editing and not win._ai_open
    text = win.editor.toPlainText()
    assert text == "# Plan\n\nintro\n\n## A\n- [x] one\n- [ ] two\n- [ ] extra\n\n## B\n- [ ] three\n"   # [x] kept
    assert p.read_text() == DOC                       # nothing written until SAVE
    assert win.undo_ai_btn.isEnabled()
    win.undo_ai()
    assert win.editor.toPlainText() == DOC and not win.undo_ai_btn.isEnabled()
    win.editor.setPlainText(text)                     # redo by hand, then save
    win.request_save()
    assert wait_for(lambda: p.read_text() == text)


def test_ai_edit_cancel_leaves_everything_untouched(app, md, fake_groq):
    p = md("plan.md", DOC)
    win = app.open_path(str(p))
    panel = _run_edit(win)
    panel.close_panel()
    assert not win._ai_open and not win.editing and p.read_text() == DOC


def test_ai_edit_refuses_result_when_file_changed_meanwhile(app, md, fake_groq):
    p = md("plan.md", DOC)
    win = app.open_path(str(p))
    fake_groq["hook"] = lambda: p.write_text(DOC + "\n- [ ] external\n")
    panel = _run_edit(win)
    assert wait_for(lambda: win._ext_version > 0, 8)          # the watcher noticed the outside edit
    panel.apply()
    assert panel.error.isVisibleTo(panel) and "changed on disk" in panel.error.text()
    assert not win.editing


def test_ai_edit_selection_in_edit_mode_changes_only_selected_text(app, md, fake_groq):
    p = md("n.md", "# T\n\nKeep this. Make this shorter please. Keep that.\n")
    win = app.open_path(str(p))
    win.edit_requested.emit(win.key)
    assert win.editing
    text = win.editor.toPlainText()
    cur = win.editor.textCursor()
    cur.setPosition(text.index("Make"))
    cur.setPosition(text.index(" Keep that"), cur.MoveMode.KeepAnchor)
    win.editor.setTextCursor(cur)
    fake_groq["output"] = "Shorter."
    win.open_ai()
    panel = win.ai_panel
    assert panel.rb_sel.isChecked() and panel.rb_sel.isEnabled()
    panel.instruction.setPlainText("shorten")
    panel.start()
    assert wait_for(lambda: panel.stack.currentIndex() == 2)
    panel.apply()
    assert win.editor.toPlainText() == "# T\n\nKeep this. Shorter. Keep that.\n"


def test_ai_edit_error_returns_to_input_with_message(app, md, monkeypatch):
    from app.ai.groq import GroqError

    def boom(*a, **k):
        raise GroqError("Can't reach Groq. Check your internet connection and try again.")

    monkeypatch.setattr("app.ai.worker.stream_chat", boom)
    win = app.open_path(str(md("plan.md", DOC)))
    win.open_ai()
    win.ai_panel.instruction.setPlainText("x")
    win.ai_panel.start()
    assert wait_for(lambda: "Can't reach Groq" in win.ai_panel.error.text())
    assert win.ai_panel.stack.currentIndex() == 0


def test_ai_edit_history_and_diff_animation(app, md, fake_groq):
    app.settings.set("animations", True)
    win = app.open_path(str(md("plan.md", DOC)))
    panel = _run_edit(win, "add an extra task")
    assert wait_for(lambda: panel._shown == len(panel._rows))     # diff rows revealed one by one
    doc = panel.diff_view.toPlainText()
    assert "+ - [ ] extra" in doc
    assert app.windows.ai.history()[0] == "add an extra task"


def test_ai_create_flow_writes_new_file_and_emits_path(app, tmp_path, fake_groq):
    from app.ui.ai_create_window import AiCreateWindow
    fake_groq["output"] = "TYPE: tasks\n\n# Groceries\n- [ ] milk\n"
    app.settings.set("ai_save_dir", str(tmp_path / "ai-files"))
    dlg = AiCreateWindow(app.windows.ai)
    got = []
    dlg.created.connect(got.append)
    assert dlg.stack.currentIndex() == 1                    # key already set -> prompt page
    dlg.prompt.setPlainText("groceries for the week")
    dlg.generate()
    assert wait_for(lambda: dlg.save_btn.isVisibleTo(dlg) and dlg._worker is None)
    assert dlg.run_label.text() == "DRAFT · TASK LIST"
    dlg.save()
    assert got and got[0].endswith("groceries.md")
    assert (tmp_path / "ai-files" / "groceries.md").read_text() == "# Groceries\n- [ ] milk\n"   # TYPE line stripped


def test_ai_create_asks_for_key_first(make_app):
    from app.ui.ai_create_window import AiCreateWindow
    a = make_app()
    dlg = AiCreateWindow(a.windows.ai)
    assert dlg.stack.currentIndex() == 0
    dlg.key_edit.setText("gsk_abc")
    dlg._save_key()
    assert a.windows.ai.api_key() == "gsk_abc" and dlg.stack.currentIndex() == 1


def test_key_storage_roundtrip_and_removal(app):
    ai = app.windows.ai
    ai.set_api_key("  gsk_x  ")
    assert ai.api_key() == "gsk_x"
    ai.set_api_key("")
    assert ai.api_key() == ""


def test_home_and_settings_expose_ai(app):
    from app.ui.settings_window import SettingsWindow
    assert app.home.ai_btn.text().endswith("New with AI")
    s = SettingsWindow(app.settings, "", app.windows.ai)
    s.animations.setChecked(False)
    s._custom_dir = "/tmp/x"
    s.apply()
    assert app.settings.get("animations") is False and app.settings.get("ai_save_dir") == "/tmp/x"


def test_typing_cat_paints(qapp):
    from app.ui.typing_cat import TypingCat
    cat = TypingCat("retro", True)
    cat.start()
    cat._tick()
    assert not cat.grab().isNull()
    cat.stop()

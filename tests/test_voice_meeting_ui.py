"""Meeting analyses and Markdown export through the real Voice window (fake mic, fake Whisper, fake Groq)."""
import array
import json

import pytest
from conftest import wait_for

from app.voice import session, wav
from app.voice.transcript import Word

SR = wav.SAMPLE_RATE
SUMMARY = "## Overview\nThe team agreed to ship on Friday.\n"
TAKEAWAYS = "- Ship on Friday\n- Priya owns the report\n"
ACTIONS = json.dumps([{"task": "Send the report", "owner": "Priya", "deadline": "Friday", "context": "for finance"},
                      {"task": "Book the room", "owner": "Not stated", "deadline": None, "context": None}])


def tone(ms):
    return array.array("h", [8000 if i % 2 == 0 else -8000 for i in range(SR * ms // 1000)]).tobytes()


@pytest.fixture
def ctx(make_app, monkeypatch, tmp_path):
    monkeypatch.setenv("MDC_FAKE_MIC", "1")
    monkeypatch.setattr("app.voice.whisper.transcribe", lambda k, d, o, language=None: ("Priya will send the report on Friday", [Word("Priya", o, o + 100)]))
    state = {"replies": {"summary": SUMMARY, "takeaways": TAKEAWAYS, "actions": ACTIONS}, "calls": [], "fail": None}

    def fake_stream(key, system, user, on_text, cancel=None):
        kind = "actions" if "JSON array" in system else "takeaways" if "key takeaways" in system else "summary"
        state["calls"].append((kind, user))
        if state["fail"] == kind:
            from app.ai.groq import GroqError
            raise GroqError("model unavailable")
        on_text(state["replies"][kind][:5])
        return state["replies"][kind]

    monkeypatch.setattr("app.ai.worker.stream_chat", fake_stream)
    app = make_app()
    app.windows.ai.set_api_key("gsk_test")
    app.settings.set("ai_save_dir", str(tmp_path / "notes"))
    app.settings.set("voice_consent", True)
    app.settings.set("animations", False)
    app.show_voice()
    w = app._voice_win
    w.open_setup("meeting")
    w.title_edit.setText("Sprint review")
    w._start()
    app.voice.recorder._src.stop()
    app.voice.recorder._on_pcm(tone(3000))
    w._stop()
    assert wait_for(lambda: app.voice.pending == 0 and app.voice._worker is None)
    panel = w.analysis
    assert w.analysis.isVisibleTo(w)
    return app, w, panel, state, tmp_path


def run_all(panel):
    panel.generate()
    assert wait_for(lambda: panel._worker is None and not panel._todo and len(panel.results) == 3)


def test_generating_all_three_analyses_fills_the_tabs_and_saves_them(ctx):
    app, w, panel, state, _ = ctx
    run_all(panel)
    assert [c[0] for c in state["calls"]] == ["summary", "takeaways", "actions"]       # one at a time, in order
    assert "[00:00] Priya will send the report on Friday" in state["calls"][0][1] and "Sprint review" in state["calls"][0][1]
    assert "ship on Friday" in panel.views["summary"].toPlainText()
    actions = panel.results["actions"]
    assert [i.owner for i in actions.items] == ["Priya", None]                          # "Not stated" never becomes an owner
    assert "- [ ] Book the room" in actions.markdown and "Owner: _not stated_" in actions.markdown
    saved = session.list_analyses(app.voice.root, app.voice.meta.id)
    assert sorted(a["type"] for a in saved) == ["actions", "summary", "takeaways"]
    assert panel.export_btn.isEnabled() and "✓" in panel.tabs.tabText(0)


def test_only_ticked_analyses_run_and_regenerate_keeps_the_old_version(ctx):
    app, w, panel, state, _ = ctx
    panel.checks["summary"].setChecked(False)
    panel.checks["takeaways"].setChecked(False)
    panel.generate()
    assert wait_for(lambda: panel._worker is None and "actions" in panel.results)
    assert [c[0] for c in state["calls"]] == ["actions"]
    state["replies"]["actions"] = json.dumps([{"task": "Only one now"}])
    panel._run(["actions"])
    assert wait_for(lambda: panel._worker is None and len(panel.results["actions"].items) == 1)
    assert len([a for a in session.list_analyses(app.voice.root, app.voice.meta.id) if a["type"] == "actions"]) == 2


def test_saved_analyses_come_back_when_the_session_is_reopened(ctx):
    app, w, panel, state, _ = ctx
    run_all(panel)
    w._back()
    w.open_session(app.voice.sessions()[0].id)
    assert wait_for(lambda: len(panel.results) == 3) and state["calls"].__len__() == 3
    assert "ship on Friday" in panel.views["summary"].toPlainText()


def test_a_failed_analysis_shows_the_error_and_does_not_block_a_retry(ctx):
    app, w, panel, state, _ = ctx
    state["fail"] = "takeaways"
    panel.generate()
    assert wait_for(lambda: panel._worker is None and not panel._todo)
    assert "model unavailable" in panel.error.text() and "takeaways" not in panel.results
    assert "summary" in panel.results and "actions" not in panel.results                # the rest of the queue was dropped
    state["fail"] = None
    panel._run(["takeaways"])
    assert wait_for(lambda: panel._worker is None and "takeaways" in panel.results) and not panel.error.isVisibleTo(panel)


def test_unreadable_action_items_are_reported_not_crashed(ctx):
    app, w, panel, state, _ = ctx
    state["replies"]["actions"] = "Sorry, I cannot do that."
    panel._run(["actions"])
    assert wait_for(lambda: panel._worker is None)
    assert "list of action items" in panel.error.text() and "actions" not in panel.results


def test_export_combined_new_file_in_the_default_folder_and_opened(ctx):
    app, w, panel, state, tmp = ctx
    run_all(panel)
    opened = []
    w.file_created.connect(opened.append)
    panel.export()
    d = panel._dialog
    assert [s.id for s in d.sections] == ["summary", "takeaways", "actions"] and "## Meeting summary" in d.preview.toPlainText()
    d.checks["takeaways"].setChecked(False)
    assert "## Key takeaways" not in d.preview.toPlainText()
    d.name_edit.setText("Sprint notes")
    d.save()
    files = list((tmp / "notes").glob("*.md"))
    assert len(files) == 1 and files[0].name == "sprint-notes.md" and "- [ ] Send the report" in files[0].read_text()
    assert opened == [str(files[0])]
    d.save()                                                                              # again: never overwrites
    assert sorted(p.name for p in (tmp / "notes").glob("*.md")) == ["sprint-notes-1.md", "sprint-notes.md"]


def test_export_one_file_per_section_into_a_chosen_folder(ctx):
    app, w, panel, state, tmp = ctx
    run_all(panel)
    panel.export()
    d = panel._dialog
    d.separate_radio.setChecked(True)
    assert d.new_radio.isChecked() and not d.combined_radio.isChecked()      # the two choices are independent
    d._folder = str(tmp / "elsewhere")
    d._update()
    assert d.preview.toPlainText().count("<!--") == 3 and not d.name_edit.isVisibleTo(d)
    d.save()
    names = sorted(p.name for p in (tmp / "elsewhere").glob("*.md"))
    assert len(names) == 3 and any("action-items" in n for n in names) and not (tmp / "notes").exists()


def test_append_adds_to_a_tracked_file_without_touching_what_is_there(ctx):
    app, w, panel, state, tmp = ctx
    plan = tmp / "plan.md"
    plan.write_text("# Plan\n\n- [x] done task\n- [ ] open task\n")
    app.windows.open_file(str(plan))
    run_all(panel)
    panel.export()
    d = panel._dialog
    d.append_radio.setChecked(True)
    assert d.target_box.count() == 1 and d.target_box.currentText() == "plan.md" and not d.name_edit.isVisibleTo(d)
    assert d.preview.toPlainText().startswith("## Sprint review (")
    d.save()
    text = plan.read_text()
    assert text.startswith("# Plan\n\n- [x] done task\n- [ ] open task\n\n## Sprint review (") and "### Action items" in text
    assert "Added to plan.md" in d.message.text()


def test_export_dialog_with_nothing_selected_cannot_save(ctx):
    app, w, panel, state, tmp = ctx
    run_all(panel)
    panel.export()
    d = panel._dialog
    for c in d.checks.values():
        c.setChecked(False)
    assert not d.save_btn.isEnabled() and d.preview.toPlainText() == ""


def test_analysis_is_offered_for_meetings_only(ctx):
    app, w, panel, state, _ = ctx
    w._back()
    w.open_setup("note")
    w._start()
    app.voice.recorder._src.stop()
    app.voice.recorder._on_pcm(tone(2000))
    w._stop()
    assert wait_for(lambda: app.voice.pending == 0 and app.voice._worker is None)
    assert not w.analysis.isVisibleTo(w)

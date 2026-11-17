"""Follow-up detector tab and live question suggestions through the real Voice window (fake mic, Whisper and Groq)."""
import array
import json
import time

import pytest
from conftest import wait_for

from app.voice import session, wav
from app.voice.session import SessionMeta
from app.voice.transcript import Word

SR = wav.SAMPLE_RATE
LONG = ("so the migration is going to take a while and nobody has said who will actually run it, we also have not agreed on a launch date yet, "
        "and the vendor keeps changing their api which is a risk for the whole plan, honestly we just need to pick a direction soon")
SAID = "Quick update: the report still has not gone out, we pushed it to next Tuesday. Nothing else."


def tone(ms):
    return array.array("h", [8000 if i % 2 == 0 else -8000 for i in range(SR * ms // 1000)]).tobytes()


@pytest.fixture
def ctx(make_app, monkeypatch, tmp_path):
    monkeypatch.setenv("MDC_FAKE_MIC", "1")
    spoken = {"text": SAID}
    monkeypatch.setattr("app.voice.whisper.transcribe", lambda k, d, o, language=None: (spoken["text"], [Word("Quick", o, o + 100)]))
    replies = {"followups": None, "topics": None, "questions": json.dumps([{"question": "Who will run the migration?", "reason": "No owner named.", "kind": "owner"}])}
    calls = []

    def fake_stream(key, system, user, on_text, cancel=None):
        kind = "followups" if "EARLIER meetings" in system and "compare" in system else "topics" if "keep coming up" in system else "questions"
        calls.append((kind, user))
        r = replies[kind]
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr("app.ai.worker.stream_chat", fake_stream)
    app = make_app()
    app.windows.ai.set_api_key("gsk_test")
    app.settings.set("ai_save_dir", str(tmp_path / "notes"))
    app.settings.set("voice_consent", True)
    app.settings.set("animations", False)
    app.show_voice()
    return app, app._voice_win, replies, calls, spoken, tmp_path


def earlier_meeting(app, tmp, items, summary="## Overview\nWe planned the release.", created=1_000):
    root = app.voice.root
    d = root / str(created)
    d.mkdir(parents=True)
    session.write_meta(d, SessionMeta(str(created), "Sprint 1", "meeting", created, 60_000, "stopped", "mic", 0))
    session.save_analysis(root, str(created), "actions", {"type": "actions", "createdAtMs": created, "markdown": "x", "items": items}, created)
    if summary:
        session.save_analysis(root, str(created), "summary", {"type": "summary", "createdAtMs": created, "markdown": summary}, created)


def record_meeting(app, w, ms=3000):
    w.open_setup("meeting")
    w.title_edit.setText("Sprint 2")
    w._start()
    app.voice.recorder._src.stop()
    app.voice.recorder._on_pcm(tone(ms))
    w._stop()
    assert wait_for(lambda: app.voice.pending == 0 and app.voice._worker is None)
    return w.analysis.followups


def run(panel, rethink=False):
    panel.run(rethink)
    assert wait_for(lambda: not panel._busy and panel._worker is None)


ITEMS = [{"task": "Send report", "owner": "Priya", "deadline": "Friday", "context": None},
         {"task": "Book room", "owner": None, "deadline": None, "context": None},
         {"task": "Ship the dashboard", "owner": None, "deadline": None, "context": None}]


def test_followups_read_real_checkbox_state_and_validate_quotes(ctx):
    app, w, replies, calls, spoken, tmp = ctx
    plan = tmp / "plan.md"
    plan.write_text("- [ ] Send report\n- [x] Book room\n")
    app.windows.open_file(str(plan))
    earlier_meeting(app, tmp, ITEMS)
    replies["followups"] = json.dumps([{"id": "1000-0", "status": "delayed", "evidence": "we pushed it to next Tuesday", "note": "New date."},
                                       {"id": "1000-2", "status": "resolved", "evidence": "the dashboard shipped yesterday", "note": "Made up."}])
    replies["topics"] = json.dumps([{"topic": "Release timing", "meetings": ["Sprint 1"], "evidence": "pushed it to next Tuesday"}])
    p = record_meeting(app, w)
    run(p)
    r = p.report
    states = {i.task: (i.state, i.file) for i in r.items}
    assert states == {"Send report": ("open", "plan.md"), "Book room": ("done", "plan.md"), "Ship the dashboard": ("unknown", None)}
    kinds = [c[0] for c in calls]
    assert kinds == ["followups", "topics"]
    assert "Book room" not in calls[0][1] and "Send report" in calls[0][1]                  # items already ticked are not re-checked
    by = {x.id: x for x in r.results}
    assert by["1000-0"].status == "delayed" and by["1000-0"].evidence == "we pushed it to next Tuesday"
    assert by["1000-2"].status == "not_mentioned" and r.dropped == 1                         # invented quote reset
    assert [t.topic for t in r.topics] == ["Release timing"]
    text = p.view.toPlainText()
    assert "Possibly delayed" in text and "Already ticked in your notes" in text and "Nothing was ticked" in text
    assert plan.read_text() == "- [ ] Send report\n- [x] Book room\n"                       # files are never modified
    assert any(d["type"] == "followups" for d in session.list_analyses(app.voice.root, app.voice.meta.id))


def test_saved_report_returns_when_the_session_is_reopened(ctx):
    app, w, replies, calls, spoken, tmp = ctx
    earlier_meeting(app, tmp, ITEMS[:1], summary="")
    replies["followups"] = "[]"
    p = record_meeting(app, w)
    run(p)
    n = len(calls)
    w._back()
    w.open_session(app.voice.sessions()[0].id)
    assert wait_for(lambda: p.report is not None) and len(calls) == n and "Send report" in p.view.toPlainText()


def test_no_earlier_action_items_gives_a_clear_message(ctx):
    app, w, replies, calls, spoken, tmp = ctx
    p = record_meeting(app, w)
    p.run()
    assert "No earlier action items" in p.error.text() and calls == [] and p.report is None


def test_a_topics_failure_does_not_lose_the_followup_results(ctx):
    app, w, replies, calls, spoken, tmp = ctx
    earlier_meeting(app, tmp, ITEMS[:1])
    replies["followups"] = json.dumps([{"id": "1000-0", "status": "discussed_open", "evidence": "the report still has not gone out", "note": "Still open."}])
    from app.ai.groq import GroqError
    replies["topics"] = GroqError("rate limit")
    p = record_meeting(app, w)
    run(p)
    assert p.report is not None and p.report.results[0].status == "discussed_open" and p.report.topics == [] and not p.error.isVisibleTo(p)


def test_a_followup_failure_is_reported_and_leaves_no_half_report(ctx):
    app, w, replies, calls, spoken, tmp = ctx
    earlier_meeting(app, tmp, ITEMS[:1])
    from app.ai.groq import GroqError
    replies["followups"] = GroqError("model unavailable")
    p = record_meeting(app, w)
    run(p)
    assert "model unavailable" in p.error.text() and p.report is None and p.check_btn.isVisibleTo(p)


def test_rethink_reruns_with_a_stricter_prompt_and_replaces_the_report(ctx):
    app, w, replies, calls, spoken, tmp = ctx
    earlier_meeting(app, tmp, ITEMS[:1], summary="")
    replies["followups"] = json.dumps([{"id": "1000-0", "status": "discussed_open", "evidence": "the report still has not gone out", "note": "a"}])
    p = record_meeting(app, w)
    run(p)
    assert p.rethink_btn.isVisibleTo(p) and "stricter" not in calls[0][1]
    replies["followups"] = json.dumps([{"id": "1000-0", "status": "delayed", "evidence": "we pushed it to next Tuesday", "note": "b"}])
    run(p, rethink=True)
    assert "stricter about evidence" in calls[1][1] and p.report.results[0].status == "delayed"


def test_followup_export_goes_through_the_markdown_dialog(ctx):
    app, w, replies, calls, spoken, tmp = ctx
    earlier_meeting(app, tmp, ITEMS[:1], summary="")
    replies["followups"] = "[]"
    p = record_meeting(app, w)
    run(p)
    p.export()
    p._dialog.name_edit.setText("followups")
    p._dialog.save()
    assert "Send report" in (tmp / "notes" / "followups.md").read_text()


# ---------------------------------------------------------------- question suggestions ----
def live_meeting(app, w, spoken, text=LONG):
    spoken["text"] = text
    w.open_setup("meeting")
    w.title_edit.setText("Planning")
    w._start()
    app.voice.recorder._src.stop()
    app.voice.recorder._on_pcm(tone(5000))
    app.voice.recorder._on_pcm(bytes(SR * 2))              # a pause cuts the chunk so it gets transcribed
    assert wait_for(lambda: app.voice.pending == 0 and app.voice._worker is None and any(c.text for c in app.voice.chunks))
    return w.questions


def due(strip):
    strip._last_at = time.monotonic() - 120                  # pretend a long time has passed


def settle(strip):
    assert wait_for(lambda: strip._worker is None)


def test_suggestions_are_off_by_default_and_never_call_the_ai_until_enabled(ctx):
    app, w, replies, calls, spoken, tmp = ctx
    q = live_meeting(app, w, spoken)
    assert q.isVisibleTo(w) and not q.enabled and app.settings.get("voice_questions") is False
    due(q)
    q._tick()
    assert calls == [] and q.ideas == []


def test_enabling_asks_at_a_quiet_moment_and_shows_deduplicated_ideas(ctx):
    app, w, replies, calls, spoken, tmp = ctx
    q = live_meeting(app, w, spoken)
    q.toggle.setChecked(True)
    assert app.settings.get("voice_questions") is True
    q._tick()
    assert calls == []                                         # enabling does not ask at once: the first round comes after a short wait
    due(q)
    q._tick()
    settle(q)
    assert [c[0] for c in calls] == ["questions"] and "migration" in calls[0][1]
    assert [i.question for _id, i in q.ideas] == ["Who will run the migration?"]
    due(q)
    q._last_words = 0
    q._tick()                                                  # same question again: not repeated
    settle(q)
    assert len(q.ideas) == 1 and "Already suggested" in calls[1][1]


def test_no_suggestions_while_paused_busy_transcribing_or_too_soon(ctx):
    app, w, replies, calls, spoken, tmp = ctx
    q = live_meeting(app, w, spoken)
    q.toggle.setChecked(True)
    q._last_at = time.monotonic()                              # just asked
    q._tick()
    assert calls == []
    due(q)
    app.voice.pause()
    q._tick()
    assert calls == []
    app.voice.resume()
    app.voice._queue.append(object())                          # a chunk is still waiting to be transcribed
    q._tick()
    assert calls == []
    app.voice._queue.clear()
    q._last_words = 10 ** 6                                    # not enough new words since the last round
    q._tick()
    assert calls == []


def test_rethink_replaces_ideas_with_different_ones_and_dismiss_removes_one(ctx):
    app, w, replies, calls, spoken, tmp = ctx
    q = live_meeting(app, w, spoken)
    q.toggle.setChecked(True)
    due(q)
    q._tick()
    settle(q)
    replies["questions"] = json.dumps([{"question": "What is the launch date?", "kind": "deadline"}, {"question": "What if the vendor API changes?", "kind": "risk"}])
    q.rethink()
    settle(q)
    assert "different angles" in calls[-1][1] and "Who will run the migration?" in calls[-1][1]
    assert [i.question for _id, i in q.ideas] == ["What is the launch date?", "What if the vendor API changes?"]
    q._dismiss(q.ideas[0][0])
    assert [i.question for _id, i in q.ideas] == ["What if the vendor API changes?"]
    replies["questions"] = "[]"
    q.rethink()
    settle(q)
    assert q.ideas == []                                       # nothing worth asking: the list empties


def test_a_failing_suggestion_request_is_silent_and_does_not_touch_the_recording(ctx):
    app, w, replies, calls, spoken, tmp = ctx
    from app.ai.groq import GroqError
    replies["questions"] = GroqError("boom")
    q = live_meeting(app, w, spoken)
    q.toggle.setChecked(True)
    due(q)
    q._tick()
    settle(q)
    assert q.ideas == [] and not w.status.isVisibleTo(w) and app.voice.recording and app.voice.phase == "recording"


def test_the_strip_only_appears_while_recording_a_meeting(ctx):
    app, w, replies, calls, spoken, tmp = ctx
    q = live_meeting(app, w, spoken)
    assert q.isVisibleTo(w)
    w._stop()
    assert not q.isVisibleTo(w)
    w._back()
    w.open_setup("practice")
    w._start()
    assert not q.isVisibleTo(w)
    w._stop()

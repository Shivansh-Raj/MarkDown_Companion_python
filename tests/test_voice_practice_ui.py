"""Practice mode through the real Voice window: local analytics, validated AI coaching, selection and export."""
import array
import json

import pytest
from conftest import wait_for

from app.voice import session, wav
from app.voice.transcript import Word

SR = wav.SAMPLE_RATE
SAID = "so um basically we kinda need to make the dashboard faster because users are complaining about it"
FIX = {"original": "we kinda need to make the dashboard faster", "category": "casual", "natural": "we need to speed up the dashboard",
       "professional": "We should improve dashboard performance.", "concise": "Speed up the dashboard.", "why": "Vague and casual."}
INVENTED = {"original": "we will revolutionise the synergy", "category": "wordy", "natural": "x", "professional": "y", "concise": "z", "why": "made up"}
REPLIES = {
    "sentences": json.dumps([FIX, INVENTED]),
    "clarity": json.dumps([{"quote": "users are complaining about it", "problem": "Which users?", "suggestion": "Name them."}]),
    "structure": json.dumps({"introduction": {"status": "weak", "note": "No opening."}, "mainExplanation": {"status": "present", "note": "Clear."},
                             "supportingPoints": {"status": "missing", "note": ""}, "conclusion": {"status": "missing", "note": ""}, "suggestions": ["Open with the goal"]}),
    "vocabulary": json.dumps([{"said": "kinda", "better": ["somewhat", "rather"], "why": "informal"}]),
}


def tone(ms):
    return array.array("h", [8000 if i % 2 == 0 else -8000 for i in range(SR * ms // 1000)]).tobytes()


@pytest.fixture
def ctx(make_app, monkeypatch, tmp_path):
    monkeypatch.setenv("MDC_FAKE_MIC", "1")
    words = [Word(w, i * 450, i * 450 + 380) for i, w in enumerate(SAID.split())]
    monkeypatch.setattr("app.voice.whisper.transcribe", lambda k, d, o, language=None: (SAID, [Word(w.w, w.start + o, w.end + o) for w in words]))
    state = {"calls": []}

    def fake_stream(key, system, user, on_text, cancel=None):
        kind = ("sentences" if "awkwardly structured" in system else "clarity" if "confusing, incomplete" in system
                else "structure" if "how a presentation is organised" in system else "vocabulary")
        state["calls"].append((kind, user))
        return REPLIES[kind]

    monkeypatch.setattr("app.ai.worker.stream_chat", fake_stream)
    app = make_app()
    app.windows.ai.set_api_key("gsk_test")
    app.settings.set("ai_save_dir", str(tmp_path / "notes"))
    app.settings.set("voice_consent", True)
    app.settings.set("animations", False)
    app.show_voice()
    w = app._voice_win
    w.open_setup("practice")
    w.title_edit.setText("Demo talk")
    w._start()
    app.voice.recorder._src.stop()
    app.voice.recorder._on_pcm(tone(8000))
    w._stop()
    assert wait_for(lambda: app.voice.pending == 0 and app.voice._worker is None)
    return app, w, w.practice, state, tmp_path


def coach_all(panel):
    panel.generate()
    assert wait_for(lambda: panel._worker is None and not panel._todo and len(panel.results) == 4)


def test_speaking_numbers_show_up_immediately_and_need_no_ai(ctx):
    app, w, p, state, _ = ctx
    assert w.practice_scroll.isVisibleTo(w) and not w.analysis.isVisibleTo(w)
    assert state["calls"] == []                                           # nothing was sent to the AI
    a = p.analytics
    assert a.word_count == len(SAID.split()) and a.has_timings and a.wpm
    assert sorted(f.term for f in a.fillers) == ["basically", "um"] and p.tiles["fillers"].text() == "2"
    assert "“um” × 1" in p.lists.text() and "not measured" in p.caveat.text()
    assert p.generate_btn.isEnabled() and p.export_btn.isEnabled()


def test_coach_runs_four_checks_and_drops_invented_quotes(ctx):
    app, w, p, state, _ = ctx
    coach_all(p)
    assert sorted(k for k, _ in state["calls"]) == ["clarity", "sentences", "structure", "vocabulary"]
    user = state["calls"][0][1]
    assert "[00:" not in user and SAID in user                           # quotes are validated against spoken words only
    s = p.results["sentences"]
    assert [f.original for f in s.sentences] == [FIX["original"]] and s.dropped == 1
    assert "left out" in p.sentence_scroll.widget().findChildren(type(p.caveat))[-1].text()
    assert p.results["structure"].structure.introduction.status == "weak"
    assert "Open with the goal" in p.views["structure"].toPlainText() and "somewhat" in p.views["vocabulary"].toPlainText()
    assert all("✓" in p.tabs.tabText(i) for i in range(4))
    saved = session.list_analyses(app.voice.root, app.voice.meta.id)
    assert sorted(d["type"] for d in saved) == ["clarity", "sentences", "structure", "vocabulary"]


def test_results_come_back_after_reopening_the_session(ctx):
    app, w, p, state, _ = ctx
    coach_all(p)
    n = len(state["calls"])
    w._back()
    w.open_session(app.voice.sessions()[0].id)
    assert wait_for(lambda: len(p.results) == 4) and len(state["calls"]) == n
    assert p.results["sentences"].sentences[0].professional.startswith("We should")


def test_style_switch_include_and_copy_per_sentence(ctx):
    app, w, p, state, _ = ctx
    coach_all(p)
    labels = lambda: " ".join(l.text() for l in p.sentence_scroll.widget().findChildren(type(p.caveat)))
    assert "we need to speed up the dashboard" in labels()
    p._set_style(0, "professional")
    assert "We should improve dashboard performance." in labels()
    p._pick(0, "professional", True)
    p._set_style(0, "concise")
    assert p._picked == {0: "concise"} and "Speed up the dashboard." in labels()      # an included item follows the shown style
    p._pick(0, "concise", False)
    assert p._picked == {}


def test_export_contains_the_picked_improvement_in_the_chosen_style_only(ctx):
    app, w, p, state, tmp = ctx
    coach_all(p)
    p._set_style(0, "professional")
    p._pick(0, "professional", True)
    ids = [s.id for s in p.sections()]
    assert ids == ["speaking", "sentences", "clarity", "structure", "vocabulary"]
    sentences = next(s for s in p.sections() if s.id == "sentences").markdown
    assert "**Better (professional):** We should improve dashboard performance." in sentences and "Speed up" not in sentences
    p.export()
    d = p._dialog
    assert "## Speaking analysis" in d.preview.toPlainText() and "not a rule" in d.preview.toPlainText()
    d.name_edit.setText("Demo feedback")
    d.save()
    text = (tmp / "notes" / "demo-feedback.md").read_text()
    assert "# Demo talk — presentation feedback" in text and "**Better (professional):**" in text and "**Pace**" in text


def test_a_coach_failure_is_shown_and_does_not_lose_the_numbers(ctx, monkeypatch):
    app, w, p, state, _ = ctx

    def broken(key, system, user, on_text, cancel=None):
        raise __import__("app.ai.groq", fromlist=["GroqError"]).GroqError("rate limit reached")

    monkeypatch.setattr("app.ai.worker.stream_chat", broken)
    p.generate()
    assert wait_for(lambda: p._worker is None and not p._todo)
    assert "rate limit" in p.error.text() and not p.results and p.analytics.word_count > 0
    assert p.generate_btn.isEnabled()


def test_a_quick_note_session_shows_neither_coach_nor_meeting_panels(ctx):
    app, w, p, state, _ = ctx
    w._back()
    w.open_setup("note")
    w._start()
    app.voice.recorder._src.stop()
    app.voice.recorder._on_pcm(tone(2000))
    w._stop()
    assert wait_for(lambda: app.voice.pending == 0 and app.voice._worker is None)
    assert not w.practice_scroll.isVisibleTo(w) and not w.analysis.isVisibleTo(w)

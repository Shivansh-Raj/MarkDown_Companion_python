"""Voice window end to end with the fake microphone, a fake Whisper and a fake Groq chat (no hardware, no network)."""
import array

import pytest
from conftest import wait_for

from app.voice import wav
from app.voice.transcript import Word

SR = wav.SAMPLE_RATE


def tone(ms):
    return array.array("h", [8000 if i % 2 == 0 else -8000 for i in range(SR * ms // 1000)]).tobytes()


def quiet(ms):
    return bytes(SR * ms // 1000 * 2)


@pytest.fixture
def app(make_app, monkeypatch, tmp_path):
    monkeypatch.setenv("MDC_FAKE_MIC", "1")
    monkeypatch.setattr("app.voice.whisper.transcribe", lambda key, data, off, language=None: ("buy milk tomorrow", [Word("buy", off + 10, off + 200)]))
    monkeypatch.setattr("app.ai.worker.stream_chat", lambda key, system, user, on_text, cancel=None: (on_text("# Shopping"), on_text("# Shopping\n\n- [ ] Buy milk tomorrow\n"))[1])
    a = make_app()
    a.windows.ai.set_api_key("gsk_test")
    a.settings.set("ai_save_dir", str(tmp_path / "notes"))
    a.settings.set("animations", False)
    a.show_voice()
    return a


def feed(app, pcm):
    app.voice.recorder._src.stop()
    app.voice.recorder._on_pcm(pcm)


def record_note(app):
    w = app._voice_win
    w.open_setup("note")
    app.settings.set("voice_consent", True)
    w.open_setup("note")
    w.title_edit.setText("Groceries")
    w._start()
    feed(app, tone(3000))
    w._stop()
    assert wait_for(lambda: app.voice.pending == 0 and app.voice._worker is None)
    return w


def test_home_has_a_voice_button_that_opens_the_workspace(app):
    shown = []
    app.home.voice_requested.connect(lambda: shown.append(1))
    app.home.voice_btn.click()
    assert shown == [1] and app._voice_win.isVisible()


def test_consent_is_required_before_the_recorder_controls_appear(app):
    w = app._voice_win
    w.open_setup("meeting")
    assert not w.title_edit.isVisibleTo(w) and not w.start_btn.isVisibleTo(w) and w.consent_btn.isVisibleTo(w)
    w.consent_btn.click()
    assert app.settings.get("voice_consent") is True
    assert w.start_btn.isVisibleTo(w) and not w.consent_btn.isVisibleTo(w) and w.mic_box.count() == 1


def test_record_stop_shows_transcript_and_saves_it_as_markdown(app, tmp_path):
    w = record_note(app)
    assert app.voice.phase == "stopped" and "buy milk tomorrow" in w.transcript.toPlainText()
    assert w.actions.isVisibleTo(w) and w.save_tr_btn.isEnabled() and not w.controls.isVisibleTo(w)
    opened = []
    w.file_created.connect(opened.append)
    w.save_tr_btn.click()
    files = list((tmp_path / "notes").glob("*.md"))
    assert len(files) == 1 and "transcript" in files[0].name
    text = files[0].read_text()
    assert "# Groceries — transcript" in text and "buy milk tomorrow" in text
    assert opened == [str(files[0])]


def test_make_note_streams_a_preview_then_saves_a_markdown_note(app, tmp_path):
    w = record_note(app)
    w.note_btn.click()
    assert wait_for(lambda: w._note_worker is None and "Buy milk" in w._note)
    assert w.note_view.isVisibleTo(w) and w.save_note_btn.isVisibleTo(w)
    w.save_note_btn.click()
    saved = [p for p in (tmp_path / "notes").glob("*.md") if "transcript" not in p.name]
    assert len(saved) == 1 and saved[0].read_text() == "# Shopping\n\n- [ ] Buy milk tomorrow\n"


def test_controls_pause_resume_and_the_rec_indicator(app):
    w = app._voice_win
    app.settings.set("voice_consent", True)
    w.open_setup("meeting")
    w._start()
    assert w.controls.isVisibleTo(w) and "REC" in app.voice.recorder.indicator()
    w.pause_btn.click()
    assert app.voice.phase == "paused" and "Resume" in w.pause_btn.text() and "PAUSED" in app.voice.recorder.indicator()
    w.pause_btn.click()
    assert app.voice.phase == "recording"
    w._stop()


def test_closing_the_window_never_stops_a_recording(app):
    w = app._voice_win
    app.settings.set("voice_consent", True)
    w.open_setup("meeting")
    w._start()
    w.close()
    assert not w.isVisible() and app.voice.recording
    app.voice.stop()


def test_sessions_list_filter_and_two_click_delete(app):
    w = record_note(app)
    w._back()
    assert w.stack.currentIndex() == 0 and w.sessions.count() == 1 and "Groceries" in w.sessions.item(0).text()
    w.search.setText("zzz")
    assert "No sessions match" in w.sessions.item(0).text()
    w.search.setText("")
    w.sessions.setCurrentRow(0)
    w.del_row_btn.click()
    assert w.sessions.count() == 1 and "Sure" in w.del_row_btn.text()      # first click only arms it
    w.del_row_btn.click()
    assert "No recordings yet" in w.sessions.item(0).text()


def test_a_start_error_is_shown_not_raised(app, monkeypatch):
    w = app._voice_win
    app.settings.set("voice_consent", True)
    w.open_setup("meeting")
    monkeypatch.setattr(app.voice.recorder, "start", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("No microphone found.")))
    w._start()
    assert w.status.isVisibleTo(w) and "No microphone" in w.status.text()


def test_the_voice_window_can_be_made_small_and_the_page_scrolls_instead(app):
    """The window used to refuse to shrink below 520x560; the recording view needs far less and scrolls when short."""
    w = app._voice_win
    assert w.minimumSize().width() <= 400 and w.minimumSize().height() <= 320
    app.settings.set("voice_consent", True)
    w.open_setup("meeting")
    w._start()
    w.resize(w.minimumSize())
    assert w.width() <= 400 and w.height() <= 320                          # the OS can really make it that small
    assert w.stack.currentWidget().widget() is not None                    # the session page lives in a scroll area
    w.pause_btn.click()
    w._stop()


def test_the_action_buttons_wrap_instead_of_forcing_a_wide_window(app):
    w = app._voice_win
    assert w.actions.layout().hasHeightForWidth()
    wide = w.actions.layout().heightForWidth(700)
    narrow = w.actions.layout().heightForWidth(300)
    assert narrow > wide                                                   # at 300 px the buttons take more rows
    assert w.actions.minimumSizeHint().width() < 300                       # and no single row needs ~590 px any more

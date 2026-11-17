"""VoiceService with the fake microphone and a fake Whisper: queue, persistence, failures, retry, re-transcribe."""
import array

import pytest
from conftest import wait_for

from app.ai.services import AiServices
from app.voice import session, wav
from app.voice.service import VoiceService
from app.voice.transcript import Word

SR = wav.SAMPLE_RATE


def tone(ms):
    return array.array("h", [8000 if i % 2 == 0 else -8000 for i in range(SR * ms // 1000)]).tobytes()


def quiet(ms):
    return bytes(SR * ms // 1000 * 2)


class FakeAi:
    def __init__(self, key="gsk_test"):
        self.key = key

    def api_key(self):
        return self.key


@pytest.fixture
def svc(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("MDC_FAKE_MIC", "1")
    calls = {"n": 0, "fail_first": 0, "text": "hello world"}

    def fake_transcribe(key, wav_bytes, offset_ms, language=None):
        calls["n"] += 1
        if calls["fail_first"] > 0:
            calls["fail_first"] -= 1
            from app.ai.groq import GroqError
            raise GroqError("rate limited")
        return calls["text"], [Word("hello", offset_ms + 100, offset_ms + 400), Word("world", offset_ms + 500, offset_ms + 900)]

    monkeypatch.setattr("app.voice.whisper.transcribe", fake_transcribe)
    monkeypatch.setattr("app.voice.service.time.sleep", lambda s: None)       # no real backoff in tests
    s = VoiceService(tmp_path / "voice", FakeAi())
    s.calls = calls
    yield s
    if s.recording:
        s.stop()
    wait_for(lambda: s.pending == 0, timeout=3)


def feed(s, pcm):
    s.recorder._src.stop()
    s.recorder._on_pcm(pcm)


def settled(s):
    return wait_for(lambda: s.pending == 0 and s._worker is None, timeout=5)


def test_recording_produces_a_transcript_that_is_saved_with_the_session(svc):
    meta = svc.start("Standup", "meeting")
    assert svc.phase == "recording"
    feed(svc, tone(5000) + quiet(700) + tone(1000))
    svc.stop()
    assert svc.phase == "stopped" and settled(svc)
    assert [c.status for c in svc.chunks] == ["final", "final"]
    assert svc.chunks[0].text == "hello world" and svc.chunks[0].words[0].w == "hello" and svc.chunks[1].words[0].start == svc.chunks[1].start_ms + 100
    saved = session.load_transcript(svc.root, meta.id)
    assert [c["text"] for c in saved["chunks"]] == ["hello world", "hello world"]


def test_silent_chunks_are_not_sent_to_the_transcriber(svc):
    svc.start("t", "note")
    feed(svc, quiet(3000))
    svc.stop()
    assert settled(svc)
    assert svc.calls["n"] == 0 and svc.chunks[0].status == "final" and svc.chunks[0].text == ""


def test_phantom_text_for_near_silence_is_dropped(svc):
    svc.calls["text"] = "Thank you."
    svc.start("t", "note")
    feed(svc, tone(500))                 # 0.5 s of sound: under the phantom threshold
    svc.stop()
    assert settled(svc) and svc.chunks[0].text == ""


def test_failures_retry_then_report_and_can_be_retried_by_hand(svc):
    svc.calls["fail_first"] = 3          # all three automatic attempts fail
    svc.start("t", "note")
    feed(svc, tone(2000))
    svc.stop()
    assert settled(svc)
    c = svc.chunks[0]
    assert c.status == "failed" and "rate limited" in c.error and svc.calls["n"] == 3
    svc.retry(0)
    assert settled(svc) and svc.chunks[0].status == "final" and svc.chunks[0].text == "hello world"
    svc.retry(0)                         # already final: nothing happens
    assert svc.calls["n"] == 4


def test_a_transient_failure_is_retried_automatically(svc):
    svc.calls["fail_first"] = 1
    svc.start("t", "note")
    feed(svc, tone(2000))
    svc.stop()
    assert settled(svc) and svc.chunks[0].status == "final" and svc.calls["n"] == 2


def test_missing_api_key_marks_chunks_failed_with_a_clear_message(svc):
    svc.ai.key = ""
    svc.start("t", "note")
    feed(svc, tone(2000))
    svc.stop()
    assert settled(svc) and svc.chunks[0].status == "failed" and "No Groq API key" in svc.chunks[0].error and svc.calls["n"] == 0


def test_open_session_loads_saved_transcript_and_retranscribe_rebuilds_from_audio(svc):
    meta = svc.start("t", "meeting")
    feed(svc, tone(5000) + quiet(700) + tone(1000))
    svc.stop()
    assert settled(svc)
    svc.reset()
    assert svc.phase == "idle" and svc.chunks == []
    svc.open_session(svc.sessions()[0])
    assert svc.phase == "stopped" and len(svc.chunks) == 2 and svc.chunks[0].text == "hello world"
    # lose the transcript (as for a recovered recording) and rebuild it from the saved audio chunks
    (svc.root / meta.id / "transcript.json").unlink()
    svc.open_session(svc.sessions()[0])
    assert svc.chunks == []
    before = svc.calls["n"]
    svc.retranscribe()
    assert settled(svc) and len(svc.chunks) == 2 and svc.calls["n"] == before + 2
    assert svc.chunks[1].start_ms == svc.chunks[0].end_ms


def test_retranscribe_without_audio_explains_why(svc):
    meta = svc.start("t", "note")
    feed(svc, tone(2000))
    svc.stop()
    assert settled(svc)
    svc.delete_audio()
    with pytest.raises(RuntimeError, match="deleted"):
        svc.retranscribe()
    assert session.load_transcript(svc.root, meta.id) is not None          # transcript survives audio deletion


def test_deleting_and_the_recording_guard(svc):
    svc.start("t", "note")
    with pytest.raises(RuntimeError, match="Stop the recording"):
        svc.delete_session()
    feed(svc, tone(2000))
    meta = svc.stop()
    assert settled(svc)
    svc.delete_session()
    assert svc.phase == "idle" and not (svc.root / meta.id).exists() and svc.sessions() == []


def test_indicator_text_follows_the_recorder(svc):
    texts = []
    svc.indicator.connect(texts.append)
    svc.start("t", "note")
    assert "REC" in svc.recorder.indicator()
    svc.pause()
    assert "PAUSED" in svc.recorder.indicator()
    svc.stop()
    assert texts[-1] == ""

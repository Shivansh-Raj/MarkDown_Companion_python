"""Recorder behaviour with the fake microphone (no hardware): control flow, persistence, failures, recovery."""
import array

import pytest

from app.voice import session, wav
from app.voice.capture import MicRecorder, fmt_clock
from app.voice.convert import convert

SR = wav.SAMPLE_RATE


def tone(ms, amp=8000):
    return array.array("h", [amp if i % 2 == 0 else -amp for i in range(SR * ms // 1000)]).tobytes()


def quiet(ms):
    return bytes(SR * ms // 1000 * 2)


@pytest.fixture
def rec(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("MDC_FAKE_MIC", "1")
    r = MicRecorder(tmp_path / "voice")
    events = {"chunks": [], "notices": [], "states": []}
    r.chunk_ready.connect(lambda *a: events["chunks"].append(a))
    r.notice.connect(lambda k, m: events["notices"].append((k, m)))
    r.state_changed.connect(events["states"].append)
    r.events = events
    yield r
    if r.active:
        r.stop()


def feed(r, pcm):
    r._src.stop()                  # stop the fake timer so only the test's audio is recorded
    r._on_pcm(pcm)


def test_full_flow_writes_audio_chunks_and_metadata(rec):
    meta = rec.start("  Sprint review ", "meeting")
    assert meta.title == "Sprint review" and meta.status == "recording" and rec.active
    feed(rec, tone(5000) + quiet(700) + tone(1000))
    done = rec.stop()
    d = rec.root / meta.id
    assert done.status == "stopped" and abs(done.duration_ms - 6700) <= 20 and done.chunks == 2
    assert [c[0] for c in rec.events["chunks"]] == [0, 1]
    assert rec.events["chunks"][0][1] == 0 and rec.events["states"] == ["recording", "stopped"]
    audio = (d / "audio.wav").read_bytes()
    assert int.from_bytes(audio[40:44], "little") == len(audio) - 44 == (SR * 6700 // 1000) * 2
    assert (d / "chunks" / "0000.wav").exists() and (d / "chunks" / "0001.wav").exists()
    assert session.read_meta(d).status == "stopped"


def test_paused_time_is_not_recorded_or_counted(rec):
    rec.start("t", "note")
    feed(rec, tone(2000))
    rec.pause()
    assert rec.paused and "PAUSED" in rec.indicator()
    rec._on_pcm(tone(5000))                    # audio arriving while paused is dropped
    rec.resume()
    assert not rec.paused and "REC" in rec.indicator()
    rec._src.stop()
    rec._on_pcm(tone(1000))
    done = rec.stop()
    assert abs(done.duration_ms - 3000) <= 20
    assert [c[0] for c in rec.events["chunks"]] == [0, 1]       # pausing ended the first chunk
    assert rec.events["chunks"][1][1] == 2000                    # timeline continues from active time


def test_device_failure_pauses_keeps_audio_and_can_resume(rec):
    rec.start("t", "meeting")
    feed(rec, tone(2000))
    rec._on_failed("device removed")
    assert rec.paused and rec.meta.status == "paused"
    assert rec.events["notices"][-1][0] == "error" and "saved" in rec.events["notices"][-1][1]
    assert rec.events["chunks"] and rec.events["chunks"][0][0] == 0
    rec.resume()
    assert not rec.paused
    done = rec.stop()
    assert done.duration_ms >= 1980


def test_silence_warning_after_three_seconds_of_nothing(rec):
    rec.start("t", "note")
    feed(rec, quiet(3500))
    warnings = [m for k, m in rec.events["notices"] if k == "warning"]
    assert len(warnings) == 1 and "Microphone access" in warnings[0]
    rec._on_pcm(quiet(1000))
    assert len([1 for k, _ in rec.events["notices"] if k == "warning"]) == 1      # only once


def test_guards(rec):
    rec.start("t", "note")
    with pytest.raises(RuntimeError):
        rec.start("again", "note")
    rec.stop()
    with pytest.raises(ValueError):
        rec.start("t", "karaoke")
    assert rec.stop() is None and rec.pause() is None


def test_killed_recording_is_recovered_on_next_launch(rec):
    meta = rec.start("t", "meeting")
    feed(rec, tone(2000))
    rec._wav.flush()
    rec._wav.abandon()                         # as if the app was killed: header never patched
    rec._src = rec._wav = None
    rec.meta.status = "recording"
    rec._tick.stop()
    assert session.recover_interrupted(rec.root) == [meta.id]
    m = session.read_meta(rec.root / meta.id)
    assert m.status == "interrupted" and abs(m.duration_ms - 2000) <= 20
    rec.meta = None                            # the "dead" recorder is discarded


def test_clock_format():
    assert fmt_clock(65_000) == "01:05" and fmt_clock(3_725_000) == "1:02:05" and fmt_clock(-5) == "00:00"


def test_fallback_conversion_downmixes_and_resamples():
    stereo48k = array.array("h", [1000, 3000] * 4800).tobytes()          # 0.1 s of stereo at 48 kHz
    out = array.array("h"); out.frombytes(convert(stereo48k, "i16", 2, 48000))
    assert len(out) == 1600 and all(abs(v - 2000) <= 1 for v in out)
    f32 = array.array("f", [0.5] * 1600).tobytes()
    out2 = array.array("h"); out2.frombytes(convert(f32, "f32", 1, 16000))
    assert len(out2) == 1600 and abs(out2[0] - 16383) <= 1

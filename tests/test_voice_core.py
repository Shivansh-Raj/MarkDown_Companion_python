"""Audio core: WAV writer/repair, chunker, session store. Pure logic, no audio device."""
import array
import json
import math

import pytest

from app.voice import chunker, session, wav
from app.voice.chunker import Chunker, rms
from app.voice.wav import SAMPLE_RATE

SR = SAMPLE_RATE


def tone(ms: int, amp: int = 8000) -> bytes:
    n = SR * ms // 1000
    return array.array("h", [amp if i % 2 == 0 else -amp for i in range(n)]).tobytes()


def quiet(ms: int) -> bytes:
    return bytes(SR * ms // 1000 * 2)


# ---- wav ----
def test_wav_header_and_length(tmp_path):
    p = tmp_path / "a.wav"
    w = wav.WavWriter(p)
    w.write(array.array("h", [1, -2, 3, 4]).tobytes())
    w.finish()
    b = p.read_bytes()
    assert b[:4] == b"RIFF" and len(b) == 44 + 8
    assert int.from_bytes(b[40:44], "little") == 8
    assert wav.encode(b"\x07\x00\x08\x00")[40:44] == (4).to_bytes(4, "little")


def test_wav_repair_after_crash(tmp_path):
    p = tmp_path / "crash.wav"
    w = wav.WavWriter(p)
    w.write(bytes(32_000))                  # one second, never finished
    w.flush()
    w.abandon()
    assert int.from_bytes(p.read_bytes()[40:44], "little") == 0
    assert wav.repair(p) == 1000
    assert int.from_bytes(p.read_bytes()[40:44], "little") == 32_000


def test_wav_never_overwrites(tmp_path):
    p = tmp_path / "keep.wav"
    wav.WavWriter(p).finish()
    with pytest.raises(FileExistsError):
        wav.WavWriter(p)


# ---- chunker ----
def test_rms_levels():
    assert rms(quiet(100)) == 0.0
    assert math.isclose(rms(tone(100)), 8000 / 32768, rel_tol=0.01)
    assert rms(b"") == 0.0


def test_cuts_at_pause_after_minimum_length():
    c = Chunker()
    out = c.push(tone(5000))
    assert out == []                         # speech, no pause yet
    out += c.push(quiet(700))
    assert len(out) == 1 and out[0].start_ms == 0 and 4900 <= out[0].voiced_ms <= 5000


def test_short_pause_does_not_cut_early():
    c = Chunker()
    assert c.push(tone(1500) + quiet(700)) == []


def test_forced_cut_at_maximum_length():
    out = Chunker().push(tone(16_000))
    assert len(out) == 1 and 14_900 <= out[0].end_ms <= 15_100


def test_flush_returns_tail_with_increasing_index():
    c = Chunker()
    c.push(tone(5000)); c.push(quiet(700)); c.push(tone(1000))
    tail = c.flush()
    assert tail.index == 1 and tail.start_ms >= 5600
    assert c.flush() is None


def test_silent_chunk_has_no_voice_and_active_time_counts_samples():
    c = Chunker()
    c.push(quiet(3000))
    assert c.active_ms == 3000
    assert c.flush().voiced_ms == 0


# ---- sessions ----
def meta(i="abc", status="stopped", t=1):
    return session.SessionMeta(id=i, title="t", mode="meeting", created_at_ms=t, status=status, device="mic")


def test_ids_are_validated(tmp_path):
    assert session.is_valid_id("1731000000000")
    for bad in ("../etc", "", "a/b", "x" * 65):
        assert not session.is_valid_id(bad)
        with pytest.raises(ValueError):
            session.session_dir(tmp_path, bad)


def test_recovery_marks_interrupted_and_repairs_audio(tmp_path):
    d = tmp_path / "abc"
    (d / "chunks").mkdir(parents=True)
    (d / "chunks" / "0000.wav").write_bytes(b"x")
    w = wav.WavWriter(d / "audio.wav")
    w.write(bytes(64_000)); w.flush(); w.abandon()
    session.write_meta(d, meta("abc", "recording"))
    assert session.recover_interrupted(tmp_path) == ["abc"]
    m = session.read_meta(d)
    assert m.status == "interrupted" and m.duration_ms == 2000 and m.chunks == 1
    assert session.recover_interrupted(tmp_path) == []        # idempotent


def test_stopped_sessions_untouched_and_list_newest_first(tmp_path):
    for i, t in (("a", 1), ("b", 5)):
        (tmp_path / i).mkdir()
        session.write_meta(tmp_path / i, meta(i, "stopped", t))
    (tmp_path / "junk").mkdir()                              # folder without session.json is ignored
    assert session.recover_interrupted(tmp_path) == []
    assert [m.id for m in session.list_sessions(tmp_path)] == ["b", "a"]


def test_transcript_and_analyses_roundtrip_and_never_overwrite(tmp_path):
    (tmp_path / "s1").mkdir()
    assert session.load_transcript(tmp_path, "s1") is None
    session.save_transcript(tmp_path, "s1", {"version": 1, "chunks": []})
    assert session.load_transcript(tmp_path, "s1") == {"version": 1, "chunks": []}
    session.save_analysis(tmp_path, "s1", "summary", {"markdown": "old"}, 1)
    session.save_analysis(tmp_path, "s1", "summary", {"markdown": "new"}, 2)
    (tmp_path / "s1" / "analyses" / "broken.json").write_text("{nope")
    assert [a["markdown"] for a in session.list_analyses(tmp_path, "s1")] == ["old", "new"]
    with pytest.raises(ValueError):
        session.save_analysis(tmp_path, "s1", "../x", {}, 3)


def test_delete_audio_keeps_transcript_and_delete_session_removes_all(tmp_path):
    d = tmp_path / "s1"
    (d / "chunks").mkdir(parents=True)
    (d / "audio.wav").write_bytes(b"x")
    session.write_meta(d, meta("s1"))
    session.save_transcript(tmp_path, "s1", {"chunks": []})
    session.delete_audio(tmp_path, "s1")
    assert not (d / "audio.wav").exists() and not (d / "chunks").exists()
    assert session.load_transcript(tmp_path, "s1") is not None
    session.delete_session(tmp_path, "s1")
    assert not d.exists()
    assert json.loads(json.dumps({"ok": True}))                # (sanity: stdlib json available)

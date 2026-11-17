"""Transcript helpers and the Whisper client's pure parts (ported from V1's vitest cases)."""
import datetime

from app.voice import transcript as t
from app.voice import whisper as w
from app.voice.session import SessionMeta

MS = int(datetime.datetime(2026, 11, 17, 10).timestamp() * 1000)


def chunk(i, text, status="final"):
    return t.TranscriptChunk(i, i * 5000, i * 5000 + 5000, text, [], status)


def test_chunks_stay_ordered_and_replace_by_index():
    lst = []
    for c in (chunk(2, "c"), chunk(0, "a"), chunk(1, "b", "pending"), chunk(1, "b")):
        lst = t.upsert_chunk(lst, c)
    assert [c.text for c in lst] == ["a", "b", "c"]


def test_text_joins_only_final_chunks_and_counts_words():
    assert t.transcript_text([chunk(0, "Hello"), chunk(1, "later", "pending"), chunk(2, " world ")]) == "Hello world"
    assert t.word_count("  one two  three ") == 3 and t.word_count("") == 0


def test_clock_and_phantom_text():
    assert t.format_clock(65_000) == "01:05" and t.format_clock(3_725_000) == "1:02:05"
    assert t.is_phantom("Thank you.", 300) and not t.is_phantom("Thank you.", 4000) and not t.is_phantom("The budget", 300)


def test_file_names_and_transcript_document():
    meta = SessionMeta("1", "Sprint review", "meeting", MS, 125_000, "stopped", "mic", 2)
    assert t.date_stamp(MS) == "2026-11-17" and t.suggest_file_name("Sprint review", MS) == "2026-11-17 Sprint review"
    assert t.suggest_file_name("  ", MS, "notes") == "2026-11-17 recording notes"
    md = t.transcript_markdown(meta, [chunk(0, "We shipped it."), chunk(1, "", "failed")])
    assert "# Sprint review — transcript" in md and "**Length:** 02:05" in md
    assert "**[00:00]** We shipped it." in md and "[00:05]" not in md
    assert "_No speech was transcribed._" in t.transcript_markdown(meta, [])


def test_chunk_json_roundtrip():
    c = t.TranscriptChunk(3, 1, 2, "hi", [t.Word("hi", 1, 2)], "final", 900, None)
    assert t.TranscriptChunk.from_json(c.to_json()) == c


def test_whisper_parsing_shifts_times_and_drops_blank_words():
    text, words = w.parse_whisper({"text": " hi there ", "words": [{"word": " hi", "start": 0.1, "end": 0.4}, {"word": " ", "start": 0.5, "end": 0.6},
                                                                 {"word": "there", "start": 0.5, "end": 0.9}]}, 10_000)
    assert text == "hi there" and words == [t.Word("hi", 10_100, 10_400), t.Word("there", 10_500, 10_900)]
    assert w.parse_whisper({"text": "ok"}, 0) == ("ok", []) and w.parse_whisper(None, 0) == ("", [])


def test_whisper_model_choice_never_picks_chat_models():
    assert w.pick_stt_model(["llama-3.3-70b-versatile", "whisper-large-v3", "whisper-large-v3-turbo"]) == "whisper-large-v3-turbo"
    assert w.pick_stt_model(["llama-3.3-70b-versatile"]) is None


def test_multipart_body_contains_fields_and_audio():
    body, ctype = w.multipart({"model": "m"}, "chunk.wav", b"RIFFdata")
    boundary = ctype.split("boundary=")[1]
    assert f"--{boundary}--".encode() in body and b'name="model"' in body and b'filename="chunk.wav"' in body and b"RIFFdata" in body

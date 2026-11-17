"""Speaking analytics (ported from V1's vitest cases): pace, fillers, repeats, pauses, honest limits."""
from app.voice.analytics import compute_analytics, find_fillers, norm
from app.voice.transcript import TranscriptChunk, Word


def speak(texts, start, per_word=400):
    return [Word(w, start + i * per_word, start + i * per_word + per_word - 50) for i, w in enumerate(texts)]


def chunk(words, i=0, status="final"):
    return TranscriptChunk(i, words[0].start if words else 0, words[-1].end if words else 0, " ".join(w.w for w in words), words, status)


def test_fillers_single_words_and_phrases_counted_once():
    f = find_fillers("so um you know it was basically um fine and i mean actually like good".split())
    assert f == {"um": 2, "you know": 1, "basically": 1, "i mean": 1, "actually": 1, "like": 1}
    assert norm("Um,") == "um" and norm("don't!") == "don't"


def test_pace_fillers_and_repeats_from_timed_words():
    base = "we should ship the new feature um because the new feature helps users and basically the new feature is simple".split()
    texts = (base * 6)[:100]
    a = compute_analytics([chunk(speak(texts, 0))], 40_000)
    assert a.has_timings and a.word_count == 100 and a.wpm == 150
    assert "um" in [f.term for f in a.fillers] and a.filler_total > 5
    assert any("new feature" in p.term for p in a.repeated_phrases)


def test_pauses_between_words_including_very_long_ones():
    first = speak("one two three four five six".split(), 0)
    second = speak("seven eight nine ten".split(), first[5].end + 3500)
    third = speak("eleven twelve".split(), second[3].end + 1200)
    a = compute_analytics([chunk(first + second + third)], 20_000)
    assert a.pauses.count == 2 and a.pauses.very_long == 1
    assert (a.pauses.longest[0].before, a.pauses.longest[0].after) == ("six", "seven") and a.pauses.longest[0].duration_ms >= 3500


def test_pace_windows_flag_big_changes_and_notes_say_so():
    slow = speak([f"a{i}" for i in range(30)], 0, 1000)               # 60 wpm for 30 s
    fast = speak([f"b{i}" for i in range(150)], 30_000, 200)          # 300 wpm for 30 s
    a = compute_analytics([chunk(slow + fast)], 60_000)
    assert len(a.pace.windows) == 2 and sorted(k for _w, k in a.pace.flagged) == ["faster", "slower"]
    assert any("varied a lot" in n for n in a.notes)


def test_immediate_repeats_but_not_intentional_that_that():
    a = compute_analytics([chunk(speak("the the plan is is good that that works".split(), 0))], 5000)
    assert [p for p, _t in a.stutters] == ["the the", "is is"]


def test_without_word_timestamps_no_pace_or_pause_claims_are_made():
    c = TranscriptChunk(0, 0, 10_000, "um hello there everyone", [], "final")
    a = compute_analytics([c], 10_000)
    assert not a.has_timings and a.pace is None and a.pauses is None
    assert a.word_count == 4 and a.wpm == 24 and a.filler_total == 1


def test_failed_chunks_are_ignored():
    assert compute_analytics([chunk(speak(["lost"], 0), 1, "failed")], 1000).word_count == 0

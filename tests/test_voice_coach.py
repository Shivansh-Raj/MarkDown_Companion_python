"""AI coach parsing and validation (ported from V1's vitest cases): invented quotes are dropped, Markdown is honest."""
import json

import pytest

from app.voice import coach as c
from app.voice.analytics import compute_analytics
from app.voice.transcript import TranscriptChunk, Word

TRANSCRIPT = "So basically the thing is that we kinda need to, um, make the dashboard faster because users are complaining about it."
T = c.squash(TRANSCRIPT)


def test_extract_json_handles_fences_chatter_and_brackets_in_strings():
    assert c.extract_json('Sure!\n```json\n[{"a":"x ] y"}]\n```') == [{"a": "x ] y"}]
    assert c.extract_json('{"k": [1, {"z": 2}]} trailing') == {"k": [1, {"z": 2}]}
    with pytest.raises(ValueError, match="structured"):
        c.extract_json("no json at all")
    with pytest.raises(ValueError, match="unreadable"):
        c.extract_json("[1, 2")


def test_quote_check_ignores_case_and_punctuation_and_rejects_inventions():
    assert c.in_transcript("Make the dashboard faster", T) and c.in_transcript("we kinda need to, um, make", T)
    assert not c.in_transcript("we must accelerate the dashboard", T) and not c.in_transcript("", T)


def test_sentence_fixes_quoting_unsaid_words_are_dropped():
    raw = json.dumps([
        {"original": "we kinda need to, um, make the dashboard faster", "category": "casual", "natural": "we need to speed up the dashboard",
         "professional": "We should improve dashboard performance.", "concise": "Speed up the dashboard.", "why": "Filler and vague."},
        {"original": "we will revolutionise the synergy", "category": "wordy", "natural": "x", "professional": "y", "concise": "z", "why": "made up"}])
    r = c.to_result("sentences", raw, TRANSCRIPT, False, 1)
    assert len(r.sentences) == 1 and r.dropped == 1 and "performance" in r.sentences[0].professional
    assert "left out because they did not match" in c.coach_markdown(r)


def test_missing_styles_fall_back_to_natural_and_bad_categories_default():
    f = c.to_result("sentences", '[{"original":"the thing is that we kinda need","natural":"we need","category":"???"}]', TRANSCRIPT, False, 1).sentences[0]
    assert (f.category, f.professional, f.concise) == ("awkward", "we need", "we need")


def test_clarity_and_vocabulary_are_validated_too():
    cl = c.to_result("clarity", json.dumps([{"quote": "users are complaining about it", "problem": "Which users?", "suggestion": "Name them."},
                                            {"quote": "invented", "problem": "x", "suggestion": "y"}]), TRANSCRIPT, False, 1)
    assert len(cl.clarity) == 1 and cl.dropped == 1
    v = c.to_result("vocabulary", json.dumps([{"said": "thing", "better": ["issue", "point"], "why": "vague"}, {"said": "zzz", "better": ["a"]}]), TRANSCRIPT, False, 1)
    assert v.vocabulary == [c.VocabSuggestion("thing", ["issue", "point"], "vague")]


def test_structure_report_normalises_statuses_and_rejects_lists():
    raw = json.dumps({"introduction": {"status": "weak", "note": "No opening."}, "mainExplanation": {"status": "present", "note": "Clear."},
                      "supportingPoints": {"status": "bogus"}, "conclusion": {"status": "missing", "note": ""}, "suggestions": ["Open with the goal", ""]})
    s = c.to_result("structure", raw, TRANSCRIPT, False, 1).structure
    assert s.introduction.status == "weak" and s.supporting_points.status == "missing" and s.suggestions == ["Open with the goal"]
    assert "**Introduction:** Weak — No opening." in c.coach_markdown(c.CoachResult("structure", 1, structure=s))
    with pytest.raises(ValueError):
        c.to_result("structure", "[1]", TRANSCRIPT, False, 1)


def test_results_roundtrip_through_json():
    for kind, raw in (("sentences", '[{"original":"the thing is","natural":"x"}]'), ("vocabulary", '[{"said":"thing","better":["issue"]}]'),
                      ("clarity", '[{"quote":"make the dashboard faster","problem":"p"}]'),
                      ("structure", '{"introduction":{"status":"present","note":"ok"},"suggestions":["s"]}')):
        r = c.to_result(kind, raw, TRANSCRIPT, True, 5)
        assert c.CoachResult.from_json(json.loads(json.dumps(r.to_json()))) == r


def test_prompts_require_verbatim_quotes_natural_voice_and_no_corporate_speak():
    p = c.build_system_prompt("sentences")
    assert "EXACTLY" in p and "natural voice" in p and "corporate" in p
    with pytest.raises(ValueError):
        c.build_system_prompt("poem")


def test_selected_improvements_render_in_the_chosen_style_only():
    fixes = [c.SentenceFix("a", "casual", "n1", "p1", "c1", "w1"), c.SentenceFix("b", "wordy", "n2", "p2", "c2", "w2")]
    md = c.selected_sentences_markdown(fixes, [(1, "concise")])
    assert '**You said:** "b"' in md and "**Better (concise):** c2" in md and "n1" not in md
    assert "No improvements selected" in c.selected_sentences_markdown(fixes, [])


def test_speaking_analysis_markdown_carries_the_caveat():
    words = [Word("um" if i % 7 == 0 else f"w{i}", i * 500, i * 500 + 400) for i in range(40)]
    a = compute_analytics([TranscriptChunk(0, 0, 20_000, "", words, "final")], 20_000)
    md = c.speaking_analysis_markdown(a)
    assert "**Pace**" in md and '"um"' in md and "not a rule" in md

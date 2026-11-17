"""Follow-up detector and question suggestions (ported from V1's vitest cases)."""
import pytest

from app.voice import followups as f
from app.voice.followups import PriorItem


def item(id_, task, **kw):
    return PriorItem(id_, task, kw.get("owner"), kw.get("deadline"), "Sprint 1", "2026-11-10", kw.get("state", "unknown"), kw.get("file"))


def test_checkbox_lines_are_read_outside_code_fences():
    md = "# T\n- [x] done thing\n- [ ] open thing\n```\n- [ ] in code\n```\n1. [X] numbered"
    assert f.parse_checkboxes(md) == [(True, "done thing"), (False, "open thing"), (True, "numbered")]


def test_items_are_matched_to_real_checkbox_state_and_an_unchecked_copy_means_open():
    files = [("plan.md", "- [x] Send the budget report to finance\n- [ ] Book the room"), ("old.md", "- [ ] Send the budget report to finance")]
    out = f.match_checkbox_state([item("1", "Send the budget report to finance"), item("2", "book the room!"), item("3", "Something never written down")], files)
    assert [o.state for o in out] == ["open", "open", "unknown"] and out[0].file == "old.md"
    done = f.match_checkbox_state([item("1", "Send the budget report to finance")], files[:1])
    assert (done[0].state, done[0].file) == ("done", "plan.md")


ITEMS = [item("a", "Send report"), item("b", "Book room")]
SAID = "Quick update: the report still has not gone out, we pushed it to next Tuesday. Nothing else."


def test_statuses_without_a_verifiable_quote_reset_and_skipped_items_are_filled_in():
    import json
    raw = json.dumps([{"id": "a", "status": "delayed", "evidence": "we pushed it to next Tuesday", "note": "New date."},
                      {"id": "b", "status": "resolved", "evidence": "the room is booked and confirmed", "note": "Made up."},
                      {"id": "zzz", "status": "resolved", "evidence": "x", "note": "unknown id"}])
    results, dropped = f.to_follow_ups(raw, ITEMS, SAID)
    assert [(r.id, r.status) for r in results] == [("a", "delayed"), ("b", "not_mentioned")]
    assert results[0].evidence == "we pushed it to next Tuesday" and results[1].evidence == "" and dropped == 1
    assert all(r.status == "not_mentioned" for r in f.to_follow_ups("[]", ITEMS, SAID)[0])
    with pytest.raises(ValueError, match="list"):
        f.to_follow_ups('{"a":1}', ITEMS, SAID)


def test_recurring_topics_need_a_quote_and_at_least_one_earlier_meeting():
    import json
    raw = json.dumps([{"topic": "Release date", "meetings": ["Sprint 1"], "evidence": "pushed it to next Tuesday"},
                      {"topic": "Hiring", "meetings": ["Sprint 1"], "evidence": "we are hiring three people"},
                      {"topic": "No meetings", "meetings": [], "evidence": "report"}])
    topics, dropped = f.to_topics(raw, SAID)
    assert [t.topic for t in topics] == ["Release date"] and dropped == 1


def test_report_never_claims_anything_was_ticked_and_roundtrips():
    items = [item("a", "Send report", state="open", file="plan.md", owner="Priya"), item("b", "Old done", state="done", file="plan.md")]
    r = f.FollowUpReport(1, items, [f.FollowUp("a", "delayed", "pushed it", "New date."), f.FollowUp("b", "not_mentioned", "", "")])
    md = f.follow_up_markdown(r)
    for needle in ("**Possibly delayed**", "Owner: Priya", "still unchecked in plan.md", "Already ticked in your notes", "Nothing was ticked or changed"):
        assert needle in md
    assert f.FollowUpReport.from_json(r.to_json()) == r
    assert "No earlier action items" in f.follow_up_markdown(f.FollowUpReport(1, [], []))


def test_rethink_asks_for_stricter_evidence_and_different_angles():
    assert "stricter about evidence" in f.follow_up_user_prompt(ITEMS, "t", True) and "stricter" not in f.follow_up_user_prompt(ITEMS, "t")
    assert "different angles" in f.question_user_prompt("text", ["Who owns it?"], True) and "- Who owns it?" in f.question_user_prompt("text", ["Who owns it?"], True)
    assert "not satisfied" not in f.question_user_prompt("text", [])
    assert "stricter about evidence" in f.topics_user_prompt([("S", "d", "m")], "t", True)


def test_suggestions_only_at_a_quiet_useful_moment():
    ok = dict(enabled=True, paused=False, in_flight=False, transcribing=0, since_ms=60_000, new_words=80)
    assert f.should_suggest(**ok)
    for key, bad in (("enabled", False), ("paused", True), ("in_flight", True), ("transcribing", 1), ("since_ms", 10_000), ("new_words", 5)):
        assert not f.should_suggest(**{**ok, key: bad})


def test_question_parsing_drops_repeats_and_survives_bad_replies():
    raw = '[{"question":"Who owns the migration?","reason":"No owner named.","kind":"owner"},{"question":"who owns the migration","kind":"owner"},{"question":"By when?","kind":"weird"}]'
    out = f.parse_questions(raw, ["When is the launch?"])
    assert [q.question for q in out] == ["Who owns the migration?", "By when?"] and out[1].kind == "clarify"
    assert f.parse_questions("sorry, no", []) == [] and f.parse_questions('[{"question":"When is the launch?"}]', ["when is the launch"]) == []


def test_context_is_limited_to_the_recent_tail():
    assert len(f.recent_context("word " * 2000)) <= 2400 and f.recent_context("short") == "short"
    assert "real gap" in f.question_system_prompt() and "[]" in f.question_system_prompt()

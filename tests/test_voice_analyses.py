"""Meeting analyses, templates and safe export (ported from V1's vitest cases, plus real-file append tests)."""
import datetime

import pytest

from app.services.synchronization import SynchronizationService
from app.voice import export
from app.voice import analyses as a
from app.voice import templates as t
from app.voice.session import SessionMeta
from app.voice.transcript import TranscriptChunk

MS = int(datetime.datetime(2026, 11, 17, 9).timestamp() * 1000)
META = SessionMeta("1", "Sprint review", "meeting", MS, 90_000, "stopped", "mic", 2)


def chunk(i, text, status="final"):
    return TranscriptChunk(i, i * 65_000, i * 65_000 + 60_000, text, [], status)


# ---- action items ----
def test_action_items_map_unknowns_to_none_instead_of_inventing():
    raw = ('```json\n[{"task":"Send the report","owner":"Priya","deadline":"Friday","context":"for finance"},'
           '{"task":"Book the room","owner":"Not stated","deadline":"TBD","context":null},{"task":"  "},{"owner":"x"}]\n```')
    assert a.parse_action_items(raw) == [a.ActionItem("Send the report", "Priya", "Friday", "for finance"), a.ActionItem("Book the room")]


def test_action_item_parsing_tolerates_chatter_and_rejects_non_lists():
    assert len(a.parse_action_items('Here you go: [{"task":"A"}] hope it helps')) == 1
    assert a.parse_action_items("[]") == []
    for bad in ("no list here", "[{broken"):
        with pytest.raises(ValueError):
            a.parse_action_items(bad)


def test_action_items_render_as_unchecked_boxes_with_not_stated_markers():
    md = a.action_items_markdown([a.ActionItem("Send the report", "Priya", None, "for finance")])
    assert md == "- [ ] Send the report\n  - Owner: Priya\n  - Due: _not stated_\n  - Context: for finance"
    assert "No action items" in a.action_items_markdown([])


# ---- plumbing ----
def test_timed_transcript_uses_final_chunks_only():
    assert a.timed_transcript([chunk(0, "Hello"), chunk(1, "pending", "pending"), chunk(2, "Bye")]) == "[00:00] Hello\n[02:10] Bye"


def test_long_transcripts_are_cut_on_a_line_boundary_and_flagged():
    assert a.limit_transcript("short", 100) == ("short", False)
    assert a.limit_transcript("aaaa\nbbbb\ncccc", 11) == ("aaaa\nbbbb", True)


def test_results_roundtrip_through_json_and_empty_replies_are_rejected():
    r = a.to_result("actions", '[{"task":"A","owner":"Bo"}]', True, 5)
    assert r.items == [a.ActionItem("A", "Bo")] and r.truncated and a.AnalysisResult.from_json(r.to_json()) == r
    assert a.to_result("takeaways", "```markdown\n- one\n```", False, 6).markdown == "- one"
    with pytest.raises(ValueError):
        a.to_result("summary", "   ", False, 1)


def test_latest_result_per_type_wins():
    old, new = a.AnalysisResult("summary", 1, "old"), a.AnalysisResult("summary", 2, "new")
    out = a.latest_by_type([new, old, a.AnalysisResult("actions", 1, "x", [])])
    assert out["summary"].markdown == "new" and "actions" in out


def test_prompts_forbid_inventing_owners_and_dates():
    assert "ONLY if the transcript names" in a.build_system_prompt("actions") and "Never invent" in a.build_system_prompt("summary")
    with pytest.raises(ValueError):
        a.build_system_prompt("poem")


# ---- templates ----
SUMMARY = a.AnalysisResult("summary", 1, "## Overview\nWe met.")
ACTIONS = a.AnalysisResult("actions", 1, "- [ ] Do it\n  - Owner: _not stated_\n  - Due: _not stated_", [])


def test_standalone_and_combined_documents_have_a_dated_header_and_fixed_order():
    assert "# Sprint review — Meeting summary" in t.standalone_from_section(META, t.meeting_sections([SUMMARY])[0])
    doc = t.combined_from_sections(META, "meeting notes", t.meeting_sections([ACTIONS, SUMMARY]))
    assert doc.index("## Meeting summary") < doc.index("## Action items") and "**Date:** 2026-11-17" in doc


def test_truncation_note_is_carried_into_the_document():
    r = a.AnalysisResult("summary", 1, "x", None, True)
    assert "only the first part was analysed" in t.combined_from_sections(META, "n", t.meeting_sections([r]))


def test_append_text_leaves_existing_content_alone():
    existing = "# Project\n\n- [x] done task\n- [ ] open task\n\n"
    out = t.append_to_content(existing, t.append_from_sections(META, t.meeting_sections([SUMMARY, ACTIONS])))
    assert out.startswith("# Project\n\n- [x] done task\n- [ ] open task\n\n## Sprint review (2026-11-17)") and out.endswith("\n")
    assert "### Action items" in out


# ---- export to real files ----
def test_new_files_never_overwrite_and_separate_makes_one_per_section(tmp_path):
    secs = t.meeting_sections([SUMMARY, ACTIONS])
    first = export.write_new(tmp_path, META, "meeting notes", secs, "My notes", separate=False)[0]
    second = export.write_new(tmp_path, META, "meeting notes", secs, "My notes", separate=False)[0]
    assert first != second and second.name.endswith("-1.md") and "# Sprint review — meeting notes" in first.read_text()
    paths = export.write_new(tmp_path / "sub", META, "meeting notes", secs, "", separate=True)
    assert len(paths) == 2 and "summary" in paths[0].name and "action-items" in paths[1].name


def test_append_adds_at_the_end_keeps_crlf_and_checkboxes(tmp_path):
    f = tmp_path / "plan.md"
    f.write_bytes(b"# Plan\r\n\r\n- [x] done\r\n- [ ] open\r\n")
    export.append_to_file(SynchronizationService(), f, META, t.meeting_sections([SUMMARY]))
    raw = f.read_bytes()
    assert raw.startswith(b"# Plan\r\n\r\n- [x] done\r\n- [ ] open\r\n") and b"## Sprint review (2026-11-17)" in raw
    assert b"\n" not in raw.replace(b"\r\n", b"")                       # line endings stayed consistent


def test_append_refuses_when_the_file_changes_mid_export(tmp_path):
    f = tmp_path / "plan.md"
    f.write_text("# Plan\n")

    class Racing(SynchronizationService):
        def save_content(self, path, expected_digest, text):
            f.write_text("# Plan\n- someone else edited\n")             # a concurrent edit lands first
            return super().save_content(path, expected_digest, text)

    with pytest.raises(RuntimeError, match="changed while exporting"):
        export.append_to_file(Racing(), f, META, t.meeting_sections([SUMMARY]))
    assert f.read_text() == "# Plan\n- someone else edited\n"           # nothing of ours was written


def test_append_refuses_binary_and_missing_files(tmp_path):
    bad = tmp_path / "bin.md"
    bad.write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(RuntimeError, match="not valid UTF-8"):
        export.append_to_file(SynchronizationService(), bad, META, t.meeting_sections([SUMMARY]))
    assert bad.read_bytes() == b"\xff\xfe\x00bad"
    with pytest.raises(RuntimeError, match="Cannot read"):
        export.append_to_file(SynchronizationService(), tmp_path / "nope.md", META, t.meeting_sections([SUMMARY]))

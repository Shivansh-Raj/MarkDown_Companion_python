from app.ai.create import build_system_prompt, safe_stem, split_type_line, strip_fence, title_from_markdown, write_new_file
from app.ai.diff import diff_lines, has_changes, with_context
from app.ai.edit import (Scope, build_edited_document, list_sections, locate_selection, preserve_progress,
                         scope_text, utf16_to_index)
from app.ai.groq import GroqError, parse_sse, pick_model

DOC = "# Plan\n\nintro\n\n## A\n- [x] one\n- [ ] two\n\n## B\n- [ ] three\n"


def test_pick_model_prefers_chat_models():
    assert pick_model(["whisper-large-v3", "llama-3.1-8b-instant", "openai/gpt-oss-120b"]) == "openai/gpt-oss-120b"
    assert pick_model(["whisper-large-v3", "some-new-model"]) == "some-new-model"
    assert pick_model(["whisper-large-v3", "llama-guard-4"]) is None


def test_parse_sse_handles_partial_lines():
    chunk = 'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\ndata: {"choices":[{"delta":{"content":" there"}}]}\n\ndata: [DO'
    deltas, rest, done = parse_sse(chunk)
    assert deltas == ["Hi", " there"] and rest == "data: [DO" and not done
    assert parse_sse("data: [DONE]\n")[2] is True


def test_type_line_streaming():
    assert split_type_line("TY")[2] and split_type_line("TYPE: pl")[2] and split_type_line("TYPE: plan")[2]
    assert split_type_line("TYPE: tasks\n\n# G\n- [ ] x") == ("tasks", "# G\n- [ ] x", False)
    assert split_type_line("# Hello world")[1] == "# Hello world"
    assert split_type_line("TYPE: banana\n# X")[0] is None
    assert "TYPE:" in build_system_prompt("auto") and "TYPE:" not in build_system_prompt("plan")


def test_helpers_and_file_writing(tmp_path):
    assert title_from_markdown("x\n# **Launch** plan\n") == "Launch plan"
    assert strip_fence("```markdown\n# A\n```") == "# A"
    assert safe_stem("Launch Plan: Week 1!") == "launch-plan-week-1"
    assert safe_stem("../../etc/passwd") == "etc-passwd" and safe_stem("!!!") == "untitled"
    a = write_new_file(tmp_path / "out", "My Plan", "x")
    b = write_new_file(tmp_path / "out", "My Plan", "y")
    assert a.name == "my-plan.md" and b.name == "my-plan-1.md" and a.read_text() == "x"   # never overwrites


def test_sections_ignore_fenced_headings_and_include_children():
    s = list_sections("# T\n## A\n### A1\n```\n# not heading\n```\n## B\n")
    assert [x.title for x in s] == ["T", "A", "A1", "B"] and s[1].end == s[3].start


def test_section_edit_changes_only_that_section_and_keeps_progress():
    sec = list_sections(DOC)[1]
    scope = Scope.section(sec)
    assert scope_text(DOC, scope) == "## A\n- [x] one\n- [ ] two\n\n"
    out = build_edited_document(DOC, scope, "## A\n- [ ] one\n- [ ] two\n- [ ] extra\n")
    assert out == "# Plan\n\nintro\n\n## A\n- [x] one\n- [ ] two\n- [ ] extra\n\n## B\n- [ ] three\n"
    assert out.startswith(DOC[:sec.start]) and out.endswith(DOC[sec.end:])


def test_fences_and_invented_checked_tasks():
    scope = Scope.section(list_sections(DOC)[1])
    assert "## A\n- [x] one\n\n## B" in build_edited_document(DOC, scope, "```markdown\n## A\n- [ ] one\n```")
    assert "- [ ] brand new" in build_edited_document(DOC, scope, "## A\n- [x] brand new\n")


def test_preserve_progress_reworded_and_moved():
    assert preserve_progress("- [x] a\n- [ ] b\n", "- [ ] a!\n- [ ] b\n") == "- [x] a!\n- [ ] b\n"
    assert preserve_progress("- [x] a\n- [ ] b\n", "- [ ] b\n- [ ] a\n") == "- [ ] b\n- [x] a\n"
    code = "```\n- [ ] not a task\n```\n- [x] real\n"
    assert preserve_progress(code, code.replace("[x]", "[ ]")) == code


def test_selection_scope():
    src = "# T\n\nKeep this. Make this shorter please. Keep that.\n\n- [x] done\n"
    start = src.index("Make")
    end = src.index(" Keep that")
    scope = Scope.selection(start, end)
    assert scope_text(src, scope) == "Make this shorter please."
    assert build_edited_document(src, scope, "Shorter.") == "# T\n\nKeep this. Shorter. Keep that.\n\n- [x] done\n"
    assert locate_selection(src, "Make this shorter please.") == (start, end)
    assert locate_selection("a b a b", "a b") is None and locate_selection(src, "nope") is None


def test_whole_file_keeps_trailing_newline_and_crlf_untouched_outside():
    src = "a\r\n- [x] t\r\n"
    out = build_edited_document(src, Scope.whole(), "a\n- [ ] t")
    assert out.endswith("\n") and "[x]" in out


def test_utf16_offsets():
    assert utf16_to_index("a😀b", 3) == 2 and utf16_to_index("abc", 2) == 2


def test_diff_marks_changes_and_collapses_context():
    rows = diff_lines("a\nb\nc\nd\ne\nf\ng\nh", "a\nb\nc\nd\nE\nf\ng\nh")
    assert [r.text for r in rows if r.kind == "del"] == ["e"] and [r.text for r in rows if r.kind == "add"] == ["E"]
    collapsed = with_context(rows, 1)
    assert collapsed[0].kind == "gap" and collapsed[0].hidden == 3 and has_changes(rows)
    assert not has_changes(diff_lines("x", "x"))

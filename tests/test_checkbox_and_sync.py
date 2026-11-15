import hashlib

import pytest

from app.markdown.checkbox_handler import CheckboxMismatch, parse_task, toggle_task_line
from app.services.synchronization import ChangeKind, SynchronizationService, ToggleStatus


def test_parse_task():
    assert parse_task("- [ ] a") is False and parse_task("  * [X] a") is True
    assert parse_task("1. [x] a") is True and parse_task("> - [ ] a") is False
    assert parse_task("- [] a") is None and parse_task("text [ ]") is None


def test_toggle_preserves_everything_else_incl_crlf():
    src = "# T\r\n\r\n- [ ] a\r\n- [x] b\r\n```\r\n- [ ] code\r\n```\r\nlast"
    out = toggle_task_line(src, 2, False)
    assert out == src.replace("- [ ] a", "- [x] a")
    assert toggle_task_line(out, 3, True).count("[ ]") == 2


def test_toggle_mismatch_cases():
    with pytest.raises(CheckboxMismatch):
        toggle_task_line("- [ ] a", 0, True)      # state differs
    with pytest.raises(CheckboxMismatch):
        toggle_task_line("plain", 0, False)       # not a task any more
    with pytest.raises(CheckboxMismatch):
        toggle_task_line("- [ ] a", 5, False)     # out of range


def test_checkbox_state_persisted_to_disk(md):
    p = md(text="# T\n\n- [ ] a\n- [ ] b\n")
    s = SynchronizationService()
    s.load(str(p))
    res = s.toggle_checkbox(str(p), 2, False)
    assert res.status is ToggleStatus.OK
    assert p.read_text() == "# T\n\n- [x] a\n- [ ] b\n"
    assert s.toggle_checkbox(str(p), 2, True).status is ToggleStatus.OK
    assert p.read_text() == "# T\n\n- [ ] a\n- [ ] b\n"
    assert not list(p.parent.glob(".*.tmp"))          # no temp litter


def test_modification_detection_and_echo_suppression(md):
    p = md()
    s = SynchronizationService()
    s.load(str(p))
    assert s.check(str(p)).kind is ChangeKind.UNCHANGED
    p.write_text("# changed\n")
    r = s.check(str(p))
    assert r.kind is ChangeKind.CHANGED and r.snapshot.text == "# changed\n"
    assert s.check(str(p)).kind is ChangeKind.UNCHANGED   # same content -> no re-render
    # our own write must not look like an external change afterwards
    p.write_text("- [ ] a\n")
    s.check(str(p))
    s.toggle_checkbox(str(p), 0, False)
    assert s.check(str(p)).kind is ChangeKind.UNCHANGED


def test_missing_and_restored(md):
    p = md()
    s = SynchronizationService()
    s.load(str(p))
    p.unlink()
    assert s.check(str(p)).kind is ChangeKind.MISSING
    p.write_text("back\n")
    assert s.check(str(p)).kind is ChangeKind.CHANGED


def test_conflict_external_edit_before_click_is_not_overwritten(md):
    p = md(text="- [ ] a\n- [ ] b\n")
    s = SynchronizationService()
    s.load(str(p))
    # External editor inserts a line: the task the user clicked (line 1 / "b") moves to line 2
    external = "- [ ] new\n- [ ] a\n- [ ] b\n"
    p.write_text(external)
    res = s.toggle_checkbox(str(p), 1, False)   # user clicked "b" in the stale view
    # line 1 is now "a" (also unchecked) -> toggling is a plausible mismatch the file can't detect;
    # what we guarantee is that unrelated external content is never lost:
    assert "- [ ] new" in p.read_text()
    assert res.snapshot.text == p.read_text()


def test_conflict_detected_when_state_changed_externally(md):
    p = md(text="- [ ] a\n")
    s = SynchronizationService()
    s.load(str(p))
    p.write_text("- [x] a\nextra line from other editor\n")
    res = s.toggle_checkbox(str(p), 0, False)
    assert res.status is ToggleStatus.CONFLICT
    assert p.read_text() == "- [x] a\nextra line from other editor\n"      # untouched
    assert res.snapshot.text.endswith("other editor\n")                    # UI is told the latest


def test_change_between_read_and_replace_is_retried(md, monkeypatch):
    p = md(text="- [ ] a\n")
    s = SynchronizationService()
    s.load(str(p))
    real = SynchronizationService._changed_since
    calls = {"n": 0}

    def racing(path, digest):
        calls["n"] += 1
        if calls["n"] == 1:   # an editor saves right after we read, before we replace
            open(path, "w").write("- [ ] a\nadded concurrently\n")
        return real(path, digest)
    monkeypatch.setattr(SynchronizationService, "_changed_since", staticmethod(racing))
    res = s.toggle_checkbox(str(p), 0, False)
    assert res.status is ToggleStatus.OK
    assert p.read_text() == "- [x] a\nadded concurrently\n"                 # both edits survive


def test_non_utf8_file_is_refused(md, tmp_path):
    p = tmp_path / "bin.md"
    p.write_bytes(b"- [ ] caf\xe9\n")
    s = SynchronizationService()
    s.load(str(p))
    assert s.toggle_checkbox(str(p), 0, False).status is ToggleStatus.ERROR
    assert p.read_bytes() == b"- [ ] caf\xe9\n"


def test_digest_is_sha256(md):
    p = md(text="x")
    assert SynchronizationService().load(str(p)).digest == hashlib.sha256(b"x").hexdigest()

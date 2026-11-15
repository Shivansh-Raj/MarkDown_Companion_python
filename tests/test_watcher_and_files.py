import os
import time

from conftest import wait_for
from app.core.file_manager import FileManager
from app.services.file_watcher import Debouncer


def test_debouncer_coalesces_burst():
    hits = []
    d = Debouncer(0.05, hits.append)
    for _ in range(10):
        d.trigger("k")
        time.sleep(0.005)
    time.sleep(0.2)
    assert hits == ["k"]


def _fm():
    from app.services.file_watcher import FileWatcher
    return FileManager(watcher=FileWatcher(debounce_s=0.05), poll=False)


def test_external_edit_refreshes_once(qapp, md):
    p = md(text="one\n")
    fm = _fm()
    got = []
    fm.content_changed.connect(lambda k, t: got.append(t))
    key, text = fm.open(str(p))
    assert text == "one\n"
    for i in range(5):                       # rapid burst of writes
        p.write_text(f"v{i}\n")
    assert wait_for(lambda: got and got[-1] == "v4\n")
    n = len(got)
    wait_for(lambda: False, 0.4)
    assert len(got) == n and n <= 3          # debounced, and no further spurious refreshes
    fm.shutdown()


def test_atomic_replace_by_editor_is_detected(qapp, md):
    p = md(text="old\n")
    fm = _fm()
    got = []
    fm.content_changed.connect(lambda k, t: got.append(t))
    fm.open(str(p))
    tmp = p.with_name("swap.tmp")            # VS Code / vim style save
    tmp.write_text("new\n")
    os.replace(tmp, p)
    assert wait_for(lambda: got and got[-1] == "new\n")
    fm.shutdown()


def test_own_checkbox_write_does_not_trigger_second_refresh(qapp, md):
    p = md(text="- [ ] a\n")
    fm = _fm()
    got = []
    fm.content_changed.connect(lambda k, t: got.append(t))
    key, _ = fm.open(str(p))
    fm.toggle_checkbox(key, 0, False)
    assert got == ["- [x] a\n"]
    wait_for(lambda: False, 0.5)             # let the watcher echo arrive
    assert got == ["- [x] a\n"]
    fm.shutdown()


def test_delete_then_restore(qapp, md):
    p = md(text="hello\n")
    fm = _fm()
    states, got = [], []
    fm.file_state.connect(lambda k, s, m: states.append(s))
    fm.content_changed.connect(lambda k, t: got.append(t))
    fm.open(str(p))
    p.unlink()
    assert wait_for(lambda: "missing" in states)
    p.write_text("hello again\n")
    assert wait_for(lambda: "ok" in states and got and got[-1] == "hello again\n")
    fm.shutdown()


def test_open_missing_file_raises(qapp, tmp_path):
    import pytest
    from app.core.file_manager import FileAccessError
    fm = _fm()
    with pytest.raises(FileAccessError):
        fm.open(str(tmp_path / "nope.md"))
    fm.shutdown()


def test_manual_refresh_rerenders_even_if_unchanged(qapp, md):
    p = md(text="x\n")
    fm = _fm()
    got = []
    fm.content_changed.connect(lambda k, t: got.append(t))
    key, _ = fm.open(str(p))
    fm.refresh(key)
    assert got == ["x\n"]
    fm.shutdown()

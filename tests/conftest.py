import os
import sys
import time
from pathlib import Path

# Must be set before Qt is imported anywhere.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MDC_NO_KEYRING", "1")   # tests never touch the real OS keychain
os.environ.setdefault("MDC_NO_WEBENGINE", "1")  # lightweight view; test_webengine.py opts back in
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture(autouse=True)
def _reset_text_scale(qapp):
    """The Text size setting is process-wide state: every test starts and ends at 100%."""
    from app.ui import scale
    scale.set_percent(100)
    qapp.setFont(scale.scaled_app_font())
    yield
    scale.set_percent(100)
    qapp.setFont(scale.scaled_app_font())


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "appdata"
    monkeypatch.setenv("MDC_V2_DATA_DIR", str(d))
    return d


@pytest.fixture
def md(tmp_path):
    """Factory creating temp Markdown files (never touches V1 data)."""
    def make(name="note.md", text="# Title\n\n- [ ] one\n- [x] two\n"):
        p = tmp_path / "docs" / name
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(text.encode())
        return p
    return make


def wait_for(cond, timeout=5.0):
    from PySide6.QtCore import QCoreApplication
    end = time.time() + timeout
    while time.time() < end:
        QCoreApplication.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


class FakeBackend:
    """Stands in for RegisterHotKey."""
    def __init__(self, taken=()):
        self.taken, self.registered, self.closed = set(taken), {}, False

    def register(self, hid, mods, vk):
        from app.core.shortcut_manager import ShortcutError
        if (mods, vk) in self.taken:
            raise ShortcutError("This shortcut is already in use by another application.")
        self.registered[hid] = (mods, vk)

    def unregister(self, hid):
        self.registered.pop(hid, None)

    def close(self):
        self.closed = True


@pytest.fixture
def make_app(qapp, data_dir):
    from app.core.application import CompanionApp
    apps = []

    def factory(backend=None):
        a = CompanionApp(qapp, backend=backend or FakeBackend(), with_tray=False, poll=False)
        apps.append(a)
        return a
    yield factory
    for a in apps:
        a.shutdown()
        a.home.deleteLater()                # windows from finished tests must not pile up: a live widget costs time
    from PySide6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


_exit_status = {"code": None}


def pytest_sessionfinish(session, exitstatus):
    _exit_status["code"] = int(exitstatus)


def pytest_unconfigure(config):
    """Qt + QtWebEngine can segfault while the interpreter tears down after a long session (all tests have already finished and
    the summary is printed). Exit straight away with pytest's real status so a passing run does not end in exit code 139."""
    import sys
    if _exit_status["code"] is not None:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(_exit_status["code"])

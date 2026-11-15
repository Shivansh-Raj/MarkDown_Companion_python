"""Real QtWebEngine smoke tests (skipped when WebEngine can't run, e.g. a headless CI box)."""
import os

import pytest
from conftest import wait_for

os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")


@pytest.fixture
def web_window(qapp, monkeypatch, make_app, md):
    monkeypatch.delenv("MDC_NO_WEBENGINE", raising=False)
    from app.ui.markdown_view import WEBENGINE_AVAILABLE
    if not WEBENGINE_AVAILABLE:
        pytest.skip("QtWebEngine not available")
    app = make_app()
    p = md("web.md", "# Hello\n\n" + "filler\n\n" * 80 + "- [ ] task one\n- [x] task two\n")
    w = app.open_path(str(p))
    if type(w.view).__name__ != "WebMarkdownView":
        pytest.skip("WebEngine view did not initialise")
    return app, w, p


def _js(w, script, timeout=8):
    box = []
    w.view.page().runJavaScript(script, box.append)
    assert wait_for(lambda: box, timeout)
    return box[0]


def test_renders_and_checkbox_click_writes_file(web_window):
    app, w, p = web_window
    assert wait_for(lambda: w.view._loaded, 15), "page never loaded"
    assert _js(w, "document.querySelector('h1').textContent") == "Hello"
    assert _js(w, "document.querySelectorAll('a.cb').length") == 2
    _js(w, "document.querySelector('a.cb.open').click(); 1")       # routed via the action link
    assert wait_for(lambda: "- [x] task one" in p.read_text(), 5)
    assert wait_for(lambda: _js(w, "document.querySelectorAll('a.cb.checked').length") == 2, 5)


def test_external_update_preserves_scroll(web_window):
    app, w, p = web_window
    assert wait_for(lambda: w.view._loaded, 15)
    _js(w, "window.scrollTo(0, 400); window.scrollY")
    p.write_text(p.read_text() + "\nappended line\n")
    assert wait_for(lambda: _js(w, "document.body.innerText.includes('appended line')"), 5)
    assert _js(w, "window.scrollY") > 300


def test_real_mouse_click_toggles_checkbox(web_window):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    app, w, p = web_window
    assert wait_for(lambda: w.view._loaded, 15)
    w.view.setFocus()
    # scroll the second box into view, then click its centre with a genuine mouse event
    r = _js(w, "(function(){var e=document.querySelectorAll('a.cb')[1];e.scrollIntoView();"
               "var b=e.getBoundingClientRect();return (b.x+b.width/2)+','+(b.y+b.height/2);})()")
    r = [float(t) for t in r.split(',')]
    wait_for(lambda: False, 0.3)
    target = w.view.focusProxy() or w.view
    QTest.mouseClick(target, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(int(r[0]), int(r[1])))
    assert wait_for(lambda: "- [ ] task two" in p.read_text(), 5), p.read_text()

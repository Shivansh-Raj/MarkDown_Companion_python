from conftest import wait_for
from test_webengine import web_window, _js  # noqa: F401


def test_rapid_double_click_is_not_a_conflict(web_window):
    app, w, p = web_window
    assert wait_for(lambda: w.view._loaded, 15)
    msgs = []
    app.files.toggle_failed.connect(lambda k, m: msgs.append(m))
    _js(w, "var a=document.querySelector('a.cb.open'); a.click(); a.click(); 1")
    assert wait_for(lambda: "- [x] task one" in p.read_text(), 5)
    wait_for(lambda: False, 1)
    assert msgs == []


def test_repeated_clicks_on_same_checkbox_keep_view_in_sync(web_window):
    app, w, p = web_window
    assert wait_for(lambda: w.view._loaded, 15)
    msgs = []
    app.files.toggle_failed.connect(lambda k, m: msgs.append(m))
    for i in range(5):
        want = "- [x] task one" if i % 2 == 0 else "- [ ] task one"
        _js(w, "document.querySelector('a.cb').click(); 1")
        assert wait_for(lambda: want in p.read_text(), 5), (i, msgs)
        cls = "checked" if i % 2 == 0 else "open"
        assert wait_for(lambda: _js(w, "document.querySelector('a.cb').className") == f"cb {cls}", 5), i
        wait_for(lambda: False, 0.3)
    assert msgs == []

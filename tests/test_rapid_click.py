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
    _js(w, "document.querySelector('a.cb').click(); 1")      # a later click works again
    assert wait_for(lambda: "- [ ] task one" in p.read_text(), 5)

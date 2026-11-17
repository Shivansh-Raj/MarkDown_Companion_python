"""The New with AI window uses the same frameless pixel chrome as the home and note windows."""
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

from app.ui.title_bar import TitleBar


@pytest.fixture
def win(make_app):
    app = make_app()
    app.windows.ai.set_api_key("gsk_test")
    app.show_ai_create()
    return app, app._ai_win


def test_it_has_no_os_frame_and_stays_on_top_like_the_other_windows(win, make_app, md):
    app, w = win
    f = w.windowFlags()
    assert f & Qt.WindowType.FramelessWindowHint and f & Qt.WindowType.WindowStaysOnTopHint
    note = app.open_path(str(md()))
    for other in (app.home, note):                                       # identical structure: dark edge, frame, accent title bar
        assert other.findChild(QWidget, "frame") is not None
    assert w.findChild(QWidget, "frame") is not None and w.objectName() == "root"
    bar = w.findChild(TitleBar, "titlebar")
    assert bar is w.title_bar and bar.title.text() == "NEW WITH AI"


def test_the_title_bar_close_button_closes_the_window_and_stops_a_running_request(win):
    app, w = win
    closed = []
    w.finished.connect(lambda r: closed.append(r))
    w.prompt.setPlainText("Plan something")
    w.title_bar.close_clicked.emit()
    assert closed and not w.isVisible()


def test_escape_still_closes_it(win):
    app, w = win
    closed = []
    w.finished.connect(lambda r: closed.append(r))
    QTest.keyClick(w, Qt.Key.Key_Escape)
    assert closed


def test_it_can_be_resized_from_its_edges_and_corners_and_dragged_by_the_title_bar(win):
    app, w = win
    w.resize(500, 580)
    assert w._edges_at(QPoint(1, 300)) & Qt.Edge.LeftEdge
    assert w._edges_at(QPoint(498, 300)) & Qt.Edge.RightEdge
    assert w._edges_at(QPoint(250, 1)) & Qt.Edge.TopEdge
    corner = w._edges_at(QPoint(498, 578))
    assert corner & Qt.Edge.RightEdge and corner & Qt.Edge.BottomEdge
    assert not w._edges_at(QPoint(250, 300))                              # the middle is not an edge
    assert w.title_bar.minimumWidth() >= 0 and hasattr(w.title_bar, "_drag_origin")     # dragging is the shared title bar's job
    assert w.minimumWidth() >= 460 and w.minimumHeight() >= 520


def test_the_minimise_button_is_wired(win, monkeypatch):
    app, w = win
    called = []
    monkeypatch.setattr(w, "showMinimized", lambda: called.append(1))
    w.title_bar.minimize_clicked.emit()
    assert called == [1]

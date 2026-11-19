"""Runs a blocking Notion call off the UI thread."""
from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Signal

from .client import NotionError

_live: set = set()          # keeps running workers alive until they finish


class CallWorker(QThread):
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[[], Any], parent: QObject | None = None):
        super().__init__(parent)
        self._fn = fn

    def run(self) -> None:
        try:
            self.done.emit(self._fn())
        except NotionError as e:
            self.failed.emit(str(e))
        except Exception as e:           # never let a worker thread die silently
            self.failed.emit(f"Unexpected error: {e}")


def run_async(parent: QObject | None, fn: Callable[[], Any], ok: Callable[[Any], None], err: Callable[[str], None]) -> CallWorker:
    """Calls `fn` on a worker thread; `ok(result)` or `err(message)` then runs on the UI thread.

    The worker is deliberately not a child of `parent`: closing a window while a call is running must not destroy a running
    thread. If the window is gone when the call finishes, its callback is skipped quietly."""
    w = CallWorker(fn, None)
    _live.add(w)

    def guard(cb: Callable[[Any], None]) -> Callable[[Any], None]:
        def run(value: Any) -> None:
            try:
                cb(value)
            except RuntimeError:          # the widget that asked has been deleted since
                pass
        return run
    w.done.connect(guard(ok))
    w.failed.connect(guard(err))

    def finished() -> None:
        _live.discard(w)
        w.deleteLater()
    w.finished.connect(finished)
    w.start()
    return w

"""Runs a Groq streaming request off the UI thread."""
from __future__ import annotations

import threading

from PySide6.QtCore import QThread, Signal

from .groq import Cancelled, GroqError, stream_chat, test_key


class StreamWorker(QThread):
    text = Signal(str)        # full text so far
    done = Signal(str)        # finished normally
    stopped = Signal(str)     # cancelled by the user; carries what had arrived
    failed = Signal(str)      # readable error message

    def __init__(self, api_key: str, system: str, user: str, parent=None):
        super().__init__(parent)
        self._args = (api_key, system, user)
        self._cancel = threading.Event()
        self._last = ""

    def cancel(self) -> None:
        self._cancel.set()

    def _on_text(self, full: str) -> None:
        self._last = full
        self.text.emit(full)

    def run(self) -> None:
        try:
            full = stream_chat(*self._args, on_text=self._on_text, cancel=self._cancel)
        except Cancelled:
            self.stopped.emit(self._last)
        except GroqError as e:
            self.failed.emit(str(e))
        except Exception as e:  # never let a worker thread die silently
            self.failed.emit(f"Unexpected error: {e}")
        else:
            self.done.emit(full)


class KeyTestWorker(QThread):
    ok = Signal(str)          # model that will be used
    failed = Signal(str)

    def __init__(self, api_key: str, parent=None):
        super().__init__(parent)
        self._key = api_key

    def run(self) -> None:
        try:
            self.ok.emit(test_key(self._key))
        except GroqError as e:
            self.failed.emit(str(e))
        except Exception as e:
            self.failed.emit(f"Unexpected error: {e}")

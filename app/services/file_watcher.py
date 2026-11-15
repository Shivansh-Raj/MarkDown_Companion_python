"""watchdog-based file watching, off the GUI thread, with debouncing.

We watch the *parent directory* and filter by filename, because many editors
(VS Code, Obsidian, vim, ...) save by writing a temp file and renaming it over
the original - which would orphan a watch placed on the file itself.
"""
from __future__ import annotations

import os
import threading
from typing import Callable

from PySide6.QtCore import QObject, Signal
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from ..utils.logging_config import get_logger

log = get_logger("watcher")

_RELEVANT = {"created", "modified", "moved", "deleted"}  # ignore opened/closed noise


def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p))


class Debouncer:
    """Coalesce bursts of triggers per key into one callback after `delay` seconds of quiet."""

    def __init__(self, delay: float, callback: Callable[[str], None]):
        self._delay = delay
        self._callback = callback
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def trigger(self, key: str) -> None:
        with self._lock:
            old = self._timers.pop(key, None)
            if old:
                old.cancel()
            t = threading.Timer(self._delay, self._fire, args=(key,))
            t.daemon = True
            self._timers[key] = t
            t.start()

    def _fire(self, key: str) -> None:
        with self._lock:
            self._timers.pop(key, None)
        try:
            self._callback(key)
        except Exception:  # never let a timer thread die silently
            log.exception("debounced callback failed key=%s", key)

    def cancel_all(self) -> None:
        with self._lock:
            for t in self._timers.values():
                t.cancel()
            self._timers.clear()


class _Handler(FileSystemEventHandler):
    def __init__(self, on_path: Callable[[str], None]):
        self._on_path = on_path

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory or event.event_type not in _RELEVANT:
            return
        self._on_path(_norm(os.fsdecode(event.src_path)))
        dest = getattr(event, "dest_path", "")
        if dest:
            self._on_path(_norm(os.fsdecode(dest)))


class FileWatcher(QObject):
    """Emits `changed(key)` (queued onto the GUI thread) after a debounced burst of events."""

    changed = Signal(str)
    error = Signal(str)

    def __init__(self, debounce_s: float = 0.15, parent: QObject | None = None):
        super().__init__(parent)
        self._targets: dict[str, str] = {}      # normalized path -> key
        self._dirs: dict[str, tuple[object, int]] = {}  # dir -> (watch, refcount)
        self._lock = threading.Lock()
        self._debouncer = Debouncer(debounce_s, self._emit)
        self._handler = _Handler(self._on_path)
        self._observer = Observer()
        self._started = False

    # lifecycle -------------------------------------------------------------
    def start(self) -> None:
        try:
            self._observer.start()
            self._started = True
        except Exception as exc:
            log.exception("observer failed to start")
            self.error.emit(f"File watching is unavailable ({exc}). Use Refresh to reload manually.")

    def stop(self) -> None:
        self._debouncer.cancel_all()
        if self._started:
            self._observer.stop()
            self._observer.join(timeout=2)
            self._started = False

    # watching --------------------------------------------------------------
    def watch(self, path: str, key: str) -> bool:
        npath = _norm(path)
        directory = os.path.dirname(npath)
        with self._lock:
            self._targets[npath] = key
            if directory in self._dirs:
                w, n = self._dirs[directory]
                self._dirs[directory] = (w, n + 1)
                return True
            try:
                if not self._started:
                    raise RuntimeError("observer not running")
                w = self._observer.schedule(self._handler, directory, recursive=False)
            except Exception as exc:
                log.warning("cannot watch dir=%s reason=%s", directory, exc)
                self.error.emit(f"Cannot watch '{directory}' for changes ({exc}). Use Refresh.")
                return False
            self._dirs[directory] = (w, 1)
            return True

    def unwatch(self, path: str) -> None:
        npath = _norm(path)
        directory = os.path.dirname(npath)
        with self._lock:
            if self._targets.pop(npath, None) is None:
                return
            entry = self._dirs.get(directory)
            if entry is None:
                return
            w, n = entry
            if n > 1:
                self._dirs[directory] = (w, n - 1)
            else:
                del self._dirs[directory]
                try:
                    self._observer.unschedule(w)
                except Exception:
                    log.debug("unschedule failed", exc_info=True)

    # internals (watcher threads) ------------------------------------------------
    def _on_path(self, npath: str) -> None:
        key = self._targets.get(npath)
        if key is not None:
            self._debouncer.trigger(key)

    def _emit(self, key: str) -> None:
        self.changed.emit(key)  # cross-thread emit is queued to the receiver's thread

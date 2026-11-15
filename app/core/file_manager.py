"""Qt-facing coordinator of documents: sync service + watcher + safety-net poller."""
from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal

from ..services.file_watcher import FileWatcher
from ..services.synchronization import ChangeKind, SynchronizationService, ToggleStatus
from ..utils.logging_config import get_logger
from ..utils.paths import normalize_key

log = get_logger("files")

RETRY_DELAY_MS = 300
MAX_RETRIES = 5
POLL_INTERVAL_MS = 5000   # safety net if the watcher misses events (network drives, errors)


class FileAccessError(Exception):
    pass


class FileManager(QObject):
    content_changed = Signal(str, str)   # key, text  (render this)
    file_state = Signal(str, str, str)   # key, "ok" | "missing" | "error", message
    toggle_failed = Signal(str, str)     # key, message
    watcher_error = Signal(str)

    def __init__(self, sync: SynchronizationService | None = None, watcher: FileWatcher | None = None,
                 poll: bool = True, parent: QObject | None = None):
        super().__init__(parent)
        self.sync = sync or SynchronizationService()
        self.watcher = watcher or FileWatcher()
        self._paths: dict[str, str] = {}      # key -> real path
        self._missing: set[str] = set()
        self._retries: dict[str, int] = {}
        self.watcher.changed.connect(self._on_watch_event)
        self.watcher.error.connect(self.watcher_error)
        self.watcher.start()
        self._poller = QTimer(self)
        self._poller.timeout.connect(self._poll)
        if poll:
            self._poller.start(POLL_INTERVAL_MS)

    # -- documents ---------------------------------------------------------
    @staticmethod
    def key_for(path: str) -> str:
        return normalize_key(path)

    def is_open(self, key: str) -> bool:
        return key in self._paths

    def open(self, path: str) -> tuple[str, str]:
        """Start tracking `path`; returns (key, text). Raises FileAccessError."""
        key = normalize_key(path)
        if key in self._paths:
            return key, self.sync.known(self._paths[key]).text  # type: ignore[union-attr]
        real = _real_for_io(path)
        try:
            snap = self.sync.load(real)
        except FileNotFoundError:
            raise FileAccessError(f"File not found:\n{path}") from None
        except OSError as exc:
            raise FileAccessError(f"Cannot read file:\n{path}\n\n{exc}") from None
        self._paths[key] = real
        self.watcher.watch(real, key)
        return key, snap.text

    def close(self, key: str) -> None:
        real = self._paths.pop(key, None)
        if real:
            self.watcher.unwatch(real)
            self.sync.forget(real)
        self._missing.discard(key)
        self._retries.pop(key, None)

    def shutdown(self) -> None:
        self._poller.stop()
        self.watcher.stop()

    # -- user actions --------------------------------------------------------
    def refresh(self, key: str) -> None:
        """Explicit refresh: always re-render, even if content is unchanged."""
        real = self._paths.get(key)
        if real is None:
            return
        try:
            snap = self.sync.load(real)
        except FileNotFoundError:
            self._set_missing(key)
            return
        except OSError as exc:
            self.file_state.emit(key, "error", f"Cannot read file: {exc}")
            return
        self._restored(key)
        self.content_changed.emit(key, snap.text)

    def toggle_checkbox(self, key: str, line: int, expected_checked: bool) -> None:
        real = self._paths.get(key)
        if real is None:
            return
        result = self.sync.toggle_checkbox(real, line, expected_checked)
        if result.snapshot is not None:
            self.content_changed.emit(key, result.snapshot.text)
        if result.status is not ToggleStatus.OK:
            self.toggle_failed.emit(key, result.message)

    # -- external changes --------------------------------------------------------
    def _on_watch_event(self, key: str) -> None:
        self._retries.pop(key, None)
        self._check(key)

    def _poll(self) -> None:
        for key in list(self._paths):
            self._check(key, quiet=True)

    def _check(self, key: str, quiet: bool = False) -> None:
        real = self._paths.get(key)
        if real is None:
            return
        res = self.sync.check(real)
        if res.kind is ChangeKind.CHANGED:
            self._retries.pop(key, None)
            self._restored(key)
            self.content_changed.emit(key, res.snapshot.text)  # type: ignore[union-attr]
        elif res.kind is ChangeKind.UNCHANGED:
            self._restored(key)
        elif res.kind is ChangeKind.MISSING:
            self._set_missing(key)
        elif res.kind is ChangeKind.ERROR and not quiet:
            n = self._retries.get(key, 0)
            if n < MAX_RETRIES:
                self._retries[key] = n + 1
                QTimer.singleShot(RETRY_DELAY_MS, lambda k=key: self._check(k))
            else:
                self._missing.add(key)  # flagged: cleared (via "ok") once readable again
                self.file_state.emit(key, "error", f"File is inaccessible: {res.error}")

    def _set_missing(self, key: str) -> None:
        if key not in self._missing:
            self._missing.add(key)
            self.file_state.emit(
                key, "missing", "File was deleted or renamed. Waiting for it to reappear; showing last content.")

    def _restored(self, key: str) -> None:
        if key in self._missing:
            self._missing.discard(key)
            self.file_state.emit(key, "ok", "")


def _real_for_io(path: str) -> str:
    import os
    return os.path.realpath(os.fspath(path))

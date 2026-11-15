"""File synchronization core (Qt-free, thread-safe).

The Markdown file on disk is the source of truth. This service
  * reads files into immutable Snapshots (text + mtime + size + SHA-256),
  * remembers the last snapshot each window rendered ("known"),
  * decides whether a filesystem event is a real external change by comparing
    content hashes (this also silences the echo of our own writes),
  * applies checkbox toggles to the *latest* on-disk content, refusing to write
    when the target line changed under us.
"""
from __future__ import annotations

import enum
import hashlib
import os
import shutil
import threading
import time
from dataclasses import dataclass

from ..markdown.checkbox_handler import CheckboxMismatch, toggle_task_line
from ..utils.logging_config import get_logger

log = get_logger("sync")


@dataclass(frozen=True)
class Snapshot:
    text: str          # decoded for display (undecodable bytes replaced)
    mtime_ns: int
    size: int
    digest: str        # SHA-256 of the raw bytes
    valid_utf8: bool = True   # False -> display only, never rewritten


def _read_raw(path: str) -> tuple[bytes, os.stat_result]:
    with open(path, "rb") as f:
        data = f.read()
        return data, os.fstat(f.fileno())


def read_snapshot(path: str) -> Snapshot:
    data, st = _read_raw(path)
    try:
        data.decode("utf-8")
        valid = True
    except UnicodeDecodeError:
        valid = False
    return Snapshot(data.decode("utf-8", errors="replace"), st.st_mtime_ns, st.st_size,
                    hashlib.sha256(data).hexdigest(), valid)


class ChangeKind(enum.Enum):
    UNCHANGED = "unchanged"
    CHANGED = "changed"
    MISSING = "missing"
    ERROR = "error"          # temporarily inaccessible (locked, permissions, ...)


@dataclass(frozen=True)
class SyncResult:
    kind: ChangeKind
    snapshot: Snapshot | None = None
    error: str = ""


class ToggleStatus(enum.Enum):
    OK = "ok"
    CONFLICT = "conflict"    # file changed externally; user's click NOT applied
    ERROR = "error"


@dataclass(frozen=True)
class ToggleResult:
    status: ToggleStatus
    snapshot: Snapshot | None = None   # latest on-disk content after the attempt
    message: str = ""


class SynchronizationService:
    WRITE_ATTEMPTS = 3

    def __init__(self) -> None:
        self._known: dict[str, Snapshot] = {}
        self._lock = threading.RLock()

    # -- reading -----------------------------------------------------------
    def load(self, path: str) -> Snapshot:
        """Read the file and record it as what the UI is showing. Raises OSError."""
        snap = read_snapshot(path)
        with self._lock:
            self._known[path] = snap
        return snap

    def known(self, path: str) -> Snapshot | None:
        return self._known.get(path)

    def forget(self, path: str) -> None:
        with self._lock:
            self._known.pop(path, None)

    def check(self, path: str) -> SyncResult:
        """Compare disk with what the UI shows. Updates the known snapshot on change."""
        try:
            snap = read_snapshot(path)
        except FileNotFoundError:
            return SyncResult(ChangeKind.MISSING)
        except OSError as exc:
            return SyncResult(ChangeKind.ERROR, error=str(exc))
        with self._lock:
            prev = self._known.get(path)
            if prev is not None and prev.digest == snap.digest:
                return SyncResult(ChangeKind.UNCHANGED, snap)
            self._known[path] = snap
        return SyncResult(ChangeKind.CHANGED, snap)

    # -- writing -----------------------------------------------------------
    def toggle_checkbox(self, path: str, line: int, expected_checked: bool) -> ToggleResult:
        """Flip the task at `line` (0-based) if it still looks like what the user clicked.

        `expected_checked` is the state the user saw. The edit is computed from a
        fresh read, not from the (possibly stale) rendered text, and the file is
        re-verified immediately before the atomic replace.
        """
        last_error = ""
        for _ in range(self.WRITE_ATTEMPTS):
            try:
                raw, st = _read_raw(path)
            except OSError as exc:
                return ToggleResult(ToggleStatus.ERROR, self._safe_snapshot(path), f"Cannot read file: {exc}")
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                return ToggleResult(ToggleStatus.ERROR, self._safe_snapshot(path),
                                    "File is not valid UTF-8; refusing to modify it.")
            try:
                new_text = toggle_task_line(text, line, expected_checked)
            except CheckboxMismatch as exc:
                snap = self._adopt(path)
                return ToggleResult(ToggleStatus.CONFLICT, snap,
                                    f"The file changed outside the app ({exc}). Your click was not applied.")
            digest = hashlib.sha256(raw).hexdigest()
            try:
                if self._changed_since(path, digest):
                    last_error = "file changed while saving"
                    continue  # re-read and re-evaluate against the newest content
                self._atomic_write(path, new_text.encode("utf-8"), st)
            except OSError as exc:
                last_error = str(exc)
                time.sleep(0.05)
                continue
            snap = self._adopt(path)
            return ToggleResult(ToggleStatus.OK, snap)
        log.warning("toggle failed path=%s reason=%s", path, last_error)
        return ToggleResult(ToggleStatus.ERROR, self._safe_snapshot(path),
                            f"Could not save the checkbox ({last_error}). Nothing was changed.")

    def save_content(self, path: str, expected_digest: str, text: str) -> ToggleResult:
        """Write the whole text from edit mode, but only if the file still has `expected_digest`.

        On a mismatch nothing is written and the latest on-disk snapshot is returned (CONFLICT);
        the caller keeps the user's text and may save again against the new digest to deliberately
        overwrite. Original line endings (CRLF/LF) and a UTF-8 BOM are preserved.
        """
        last_error = ""
        for _ in range(self.WRITE_ATTEMPTS):
            try:
                raw, st = _read_raw(path)
            except OSError as exc:
                return ToggleResult(ToggleStatus.ERROR, self._safe_snapshot(path),
                                    f"Cannot save, the file is not readable: {exc}")
            digest = hashlib.sha256(raw).hexdigest()
            if digest != expected_digest:
                return ToggleResult(ToggleStatus.CONFLICT, self._adopt(path),
                                    "The file changed on disk while you were editing. Your text is kept; "
                                    "press SAVE again to overwrite it, or CANCEL to discard yours.")
            try:
                original = raw.decode("utf-8")
            except UnicodeDecodeError:
                return ToggleResult(ToggleStatus.ERROR, self._safe_snapshot(path),
                                    "File is not valid UTF-8; refusing to modify it.")
            crlf = original.count("\r\n")
            eol = "\r\n" if crlf and crlf * 2 >= original.count("\n") else "\n"
            body = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
            if eol != "\n":
                body = body.replace("\n", eol)
            data = (b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b"") + body.encode("utf-8")
            try:
                if self._changed_since(path, digest):
                    continue  # re-read; the next pass reports the conflict
                self._atomic_write(path, data, st)
            except OSError as exc:
                last_error = str(exc)
                time.sleep(0.05)
                continue
            return ToggleResult(ToggleStatus.OK, self._adopt(path))
        return ToggleResult(ToggleStatus.ERROR, self._safe_snapshot(path),
                            f"Could not save ({last_error or 'file kept changing'}). Your text is still in the editor.")

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _changed_since(path: str, digest: str) -> bool:
        data, _ = _read_raw(path)
        return hashlib.sha256(data).hexdigest() != digest

    @staticmethod
    def _atomic_write(path: str, data: bytes, st: os.stat_result) -> None:
        tmp = os.path.join(os.path.dirname(path), f".{os.path.basename(path)}.mdc-{os.getpid()}.tmp")
        try:
            with open(tmp, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            try:
                shutil.copymode(path, tmp)
            except OSError:
                pass
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def _adopt(self, path: str) -> Snapshot | None:
        try:
            return self.load(path)
        except OSError:
            return None

    def _safe_snapshot(self, path: str) -> Snapshot | None:
        return self._known.get(path)

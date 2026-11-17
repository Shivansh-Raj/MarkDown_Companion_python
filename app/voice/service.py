"""VoiceService: owns the recorder and the chunk -> transcript queue, independent of any window.

Closing the voice window never stops a recording; the service keeps working and the tray shows the REC state.
"""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from ..ai.groq import GroqError
from ..utils.logging_config import get_logger
from . import session, wav, whisper
from .capture import MicRecorder
from .session import SessionMeta
from .transcript import TranscriptChunk, is_phantom, upsert_chunk

log = get_logger("voice")
MIN_VOICED_MS = 300
ATTEMPTS = 3


class _TranscribeWorker(QThread):
    done = Signal(str, object)          # text, words
    failed = Signal(str)

    def __init__(self, api_key: str, wav_bytes: bytes, offset_ms: int, parent=None):
        super().__init__(parent)
        self._a = (api_key, wav_bytes, offset_ms)

    def run(self) -> None:
        err = "Transcription failed."
        for attempt in range(1, ATTEMPTS + 1):
            try:
                text, words = whisper.transcribe(*self._a)
                self.done.emit(text, words)
                return
            except GroqError as e:
                err = str(e)
            except Exception as e:      # a worker thread must never die silently
                err = f"Unexpected error: {e}"
            if attempt < ATTEMPTS:
                time.sleep(attempt)
        self.failed.emit(err)


class VoiceService(QObject):
    changed = Signal()                  # transcript, pending count or phase changed
    level = Signal(float, int, bool)
    notice = Signal(str, str)           # "error" | "warning", message
    phase_changed = Signal(str)         # "idle" | "recording" | "paused" | "stopped"
    indicator = Signal(str)             # tray text such as "● REC 03:12" ("" when not recording)

    def __init__(self, root: Path, ai, parent: QObject | None = None):
        super().__init__(parent)
        self.root, self.ai = root, ai
        self.meta: SessionMeta | None = None
        self.chunks: list[TranscriptChunk] = []
        self.phase = "idle"
        self.recorder = MicRecorder(root, self)
        self.recorder.level.connect(self.level)
        self.recorder.notice.connect(self.notice)
        self.recorder.chunk_ready.connect(self._on_chunk)
        self.recorder.state_changed.connect(self._on_recorder_state)
        self._queue: list[TranscriptChunk] = []
        self._worker: _TranscribeWorker | None = None
        self._current: TranscriptChunk | None = None
        self._ticker = QTimer(self, interval=1000)
        self._ticker.timeout.connect(lambda: self.indicator.emit(self.recorder.indicator()))

    # queries -------------------------------------------------------------------------
    @property
    def pending(self) -> int:
        return len(self._queue) + (1 if self._worker is not None else 0)

    @property
    def recording(self) -> bool:
        return self.recorder.active

    def sessions(self) -> list[SessionMeta]:
        return session.list_sessions(self.root)

    # lifecycle -----------------------------------------------------------------------
    def recover(self) -> list[str]:
        """Repair recordings interrupted by a crash or force-quit (call once at startup)."""
        return session.recover_interrupted(self.root)

    def start(self, title: str, mode: str, device: str | None = None) -> SessionMeta:
        self.chunks = []
        meta = self.recorder.start(title, mode, device)
        self.meta = meta
        self._set_phase("recording")
        self._ticker.start()
        return meta

    def pause(self) -> None:
        self.recorder.pause()

    def resume(self) -> None:
        self.recorder.resume()

    def stop(self) -> SessionMeta | None:
        meta = self.recorder.stop()
        if meta:
            self.meta = meta
        self._ticker.stop()
        self.indicator.emit("")
        return meta

    def shutdown(self) -> None:
        """App is quitting: finalise an in-progress recording so its audio file is valid."""
        if self.recorder.active:
            self.stop()
        self._persist()

    def _on_recorder_state(self, state: str) -> None:
        self._set_phase({"recording": "recording", "paused": "paused", "stopped": "stopped"}[state])

    def _set_phase(self, phase: str) -> None:
        self.phase = phase
        self.phase_changed.emit(phase)
        self.changed.emit()

    # opening and resetting ---------------------------------------------------------------
    def open_session(self, meta: SessionMeta) -> None:
        if self.recorder.active:
            return
        self.meta = meta
        data = session.load_transcript(self.root, meta.id)
        self.chunks = [TranscriptChunk.from_json(c) for c in (data or {}).get("chunks", [])]
        self._set_phase("stopped")

    def reset(self) -> None:
        if self.recorder.active:
            return
        self.meta, self.chunks = None, []
        self._set_phase("idle")

    # chunks -> transcript ------------------------------------------------------------------
    def _on_chunk(self, index: int, start_ms: int, end_ms: int, voiced_ms: int) -> None:
        base = TranscriptChunk(index, start_ms, end_ms, status="pending", voiced_ms=voiced_ms)
        if voiced_ms < MIN_VOICED_MS:
            base.status = "final"                      # silence: nothing to transcribe
            self.chunks = upsert_chunk(self.chunks, base)
            self.changed.emit()
            return
        self.chunks = upsert_chunk(self.chunks, base)
        self._enqueue(base)
        self.changed.emit()

    def _enqueue(self, chunk: TranscriptChunk) -> None:
        self._queue.append(chunk)
        self._pump()

    def _pump(self) -> None:
        if self._worker is not None or not self._queue or self.meta is None:
            return
        chunk = self._queue.pop(0)
        key = self.ai.api_key()
        if not key:
            self._finish(chunk, failed="No Groq API key. Add one in Settings or under New with AI.")
            return
        try:
            data = (session.session_dir(self.root, self.meta.id) / "chunks" / f"{chunk.index:04d}.wav").read_bytes()
        except OSError as e:
            self._finish(chunk, failed=f"The audio for this part is missing ({e.__class__.__name__}).")
            return
        self._current = chunk
        w = _TranscribeWorker(key, data, chunk.start_ms, self)
        w.done.connect(lambda text, words, c=chunk: self._finish(c, text=text, words=words))
        w.failed.connect(lambda msg, c=chunk: self._finish(c, failed=msg))
        w.finished.connect(self._worker_finished)
        self._worker = w
        w.start()

    def _worker_finished(self) -> None:
        w, self._worker = self._worker, None
        if w is not None:
            w.deleteLater()
        self.changed.emit()
        self._pump()

    def _finish(self, chunk: TranscriptChunk, text: str = "", words=None, failed: str | None = None) -> None:
        if failed is not None:
            chunk.status, chunk.error = "failed", failed
        else:
            chunk.status, chunk.error = "final", None
            chunk.text = "" if is_phantom(text, chunk.voiced_ms if chunk.voiced_ms is not None else 10 ** 9) else text
            chunk.words = list(words or [])
        self.chunks = upsert_chunk(self.chunks, chunk)
        self._persist()
        self.changed.emit()

    def _persist(self) -> None:
        if self.meta is not None and (self.root / self.meta.id).is_dir():
            try:
                session.save_transcript(self.root, self.meta.id, {"version": 1, "chunks": [c.to_json() for c in self.chunks]})
            except OSError:
                log.warning("could not save transcript", exc_info=True)

    def retry(self, index: int) -> None:
        c = next((x for x in self.chunks if x.index == index), None)
        if c is None or c.status != "failed":
            return
        c.status, c.error = "pending", None
        self._enqueue(c)
        self.changed.emit()

    def retranscribe(self) -> None:
        """Transcribe a stopped session again from its saved audio (recovered recordings, or after failures)."""
        m = self.meta
        if m is None or self.recorder.active:
            return
        chunks: list[TranscriptChunk] = []
        offset = 0
        for i in range(m.chunks):
            p = session.session_dir(self.root, m.id) / "chunks" / f"{i:04d}.wav"
            try:
                size = p.stat().st_size
            except OSError:
                break
            ms = max(size - wav.HEADER_LEN, 0) // 32                    # 16 kHz, 16-bit mono = 32 bytes per ms
            chunks.append(TranscriptChunk(i, offset, offset + ms, status="pending"))
            offset += ms
        if not chunks:
            raise RuntimeError("The audio of this recording was deleted, so it cannot be transcribed again.")
        self.chunks = chunks
        for c in chunks:
            self._queue.append(c)
        self.changed.emit()
        self._pump()

    # retention ---------------------------------------------------------------------------------
    def delete_audio(self) -> None:
        if self.meta and not self.recorder.active:
            session.delete_audio(self.root, self.meta.id)

    def delete_session(self, session_id: str | None = None) -> None:
        sid = session_id or (self.meta.id if self.meta else None)
        if sid is None or (self.recorder.active and self.recorder.meta and self.recorder.meta.id == sid):
            raise RuntimeError("Stop the recording before deleting it.")
        session.delete_session(self.root, sid)
        if self.meta and self.meta.id == sid:
            self.reset()

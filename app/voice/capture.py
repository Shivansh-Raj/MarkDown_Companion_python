"""Microphone recording: QtMultimedia source -> WAV on disk + chunks for transcription + level/timer signals.

`MDC_FAKE_MIC=1` replaces the microphone with a synthetic speech-like signal so the recorder, transcription queue and UI
can be exercised headlessly. Real-microphone behaviour (permission prompt, unplugging) needs a manual check.
"""
from __future__ import annotations

import array
import os
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from ..utils.logging_config import get_logger
from . import session, wav
from .chunker import Chunker, rms
from .convert import convert
from .session import SessionMeta

log = get_logger("voice")


def now_ms() -> int:
    return int(time.time() * 1000)


def fmt_clock(ms: int) -> str:
    s = max(ms, 0) // 1000
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


# ---------------------------------------------------------------- sources ----
class _Source(QObject):
    pcm = Signal(bytes)           # 16 kHz mono int16
    failed = Signal(str)          # the device stopped delivering audio

    def start(self) -> None: ...
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def stop(self) -> None: ...


class FakeSource(_Source):
    """Alternating 3 s of 'speech' (tone) and 1 s of silence, delivered in 100 ms slices."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._t = QTimer(self, interval=100)
        self._t.timeout.connect(self._tick)
        self._i = 0

    def _tick(self) -> None:
        speaking = (self._i % 40) < 30
        n = wav.SAMPLE_RATE // 10
        data = array.array("h", [6000 if k % 2 == 0 else -6000 for k in range(n)]) if speaking else array.array("h", bytes(n * 2))
        self._i += 1
        self.pcm.emit(data.tobytes())

    def start(self) -> None: self._t.start()
    def pause(self) -> None: self._t.stop()
    def resume(self) -> None: self._t.start()
    def stop(self) -> None: self._t.stop()


def list_devices() -> list[tuple[str, bool]]:
    """(name, is_default) for every microphone."""
    if os.environ.get("MDC_FAKE_MIC") == "1":
        return [("Fake microphone", True)]
    from PySide6.QtMultimedia import QMediaDevices
    default = QMediaDevices.defaultAudioInput().description()
    return [(d.description(), d.description() == default) for d in QMediaDevices.audioInputs()]


class QtSource(_Source):
    def __init__(self, device_name: str | None, parent=None):
        super().__init__(parent)
        from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioSource, QMediaDevices
        devs = QMediaDevices.audioInputs()
        dev = next((d for d in devs if d.description() == device_name), None) if device_name else QMediaDevices.defaultAudioInput()
        if dev is None or dev.isNull():
            raise RuntimeError(f'Microphone "{device_name}" is not available.' if device_name else "No microphone found.")
        want = QAudioFormat()
        want.setSampleRate(wav.SAMPLE_RATE); want.setChannelCount(1); want.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        self._direct = dev.isFormatSupported(want)
        fmt = want if self._direct else dev.preferredFormat()
        self._fmt = {QAudioFormat.SampleFormat.UInt8: "u8", QAudioFormat.SampleFormat.Int16: "i16",
                     QAudioFormat.SampleFormat.Int32: "i32", QAudioFormat.SampleFormat.Float: "f32"}.get(fmt.sampleFormat())
        if self._fmt is None:
            raise RuntimeError("This microphone uses an audio format that is not supported.")
        self._channels, self._rate = fmt.channelCount(), fmt.sampleRate()
        self.name = dev.description()
        self._QAudio = QAudio
        self._dev, self._qfmt, self._QAudioSource = dev, fmt, QAudioSource
        self._io = None
        self._stopping = False
        self._src = self._new_source()

    def _new_source(self):
        src = self._QAudioSource(self._dev, self._qfmt, self)
        src.stateChanged.connect(self._state)
        return src

    def _state(self, state) -> None:
        if not self._stopping and state == self._QAudio.State.StoppedState and self._src.error() != self._QAudio.Error.NoError:
            self.failed.emit(str(self._src.error()))

    def _read(self) -> None:
        raw = bytes(self._io.readAll())
        if raw:
            self.pcm.emit(raw if self._direct else convert(raw, self._fmt, self._channels, self._rate))

    def start(self) -> None:
        self._io = self._src.start()
        if self._io is None:
            raise RuntimeError("The microphone could not be opened. Check Microphone access in System Settings.")
        self._io.readyRead.connect(self._read)

    def pause(self) -> None:
        """Closes the microphone while paused (suspending would keep it open and the system's mic indicator on)."""
        self._stopping = True
        self._src.stop()
        self._src.deleteLater()
        self._src, self._io = None, None

    def resume(self) -> None:
        """Opens a fresh stream, which also recovers from a changed or re-plugged device."""
        if self._src is not None:
            return
        self._stopping = False
        self._src = self._new_source()
        self.start()

    def stop(self) -> None:
        self._stopping = True
        if self._src is not None:
            self._src.stop()


# --------------------------------------------------------------- recorder ----
class MicRecorder(QObject):
    level = Signal(float, int, bool)          # level 0..1, active ms, paused
    chunk_ready = Signal(int, int, int, int)  # index, start ms, end ms, voiced ms
    notice = Signal(str, str)                 # "error" | "warning", message
    state_changed = Signal(str)               # "recording" | "paused" | "stopped"
    finished = Signal(object)                 # SessionMeta

    def __init__(self, root: Path, parent=None):
        super().__init__(parent)
        self.root = root
        self.meta: SessionMeta | None = None
        self._dir: Path | None = None
        self._src: _Source | None = None
        self._wav: wav.WavWriter | None = None
        self._chunker = Chunker()
        self._paused = False
        self._level = 0.0
        self._peak = 0
        self._silent_warned = False
        self._tick = QTimer(self, interval=100)
        self._tick.timeout.connect(self._emit_level)

    # state ---------------------------------------------------------------------
    @property
    def active(self) -> bool:
        return self.meta is not None and self.meta.status in ("recording", "paused")

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def active_ms(self) -> int:
        return self._chunker.active_ms

    def indicator(self) -> str:
        """Text for the tray tooltip / REC pill."""
        if not self.active:
            return ""
        return f"{'❚❚ PAUSED' if self._paused else '● REC'} {fmt_clock(self.active_ms)}"

    # control -------------------------------------------------------------------
    def start(self, title: str, mode: str, device: str | None = None) -> SessionMeta:
        if self.active:
            raise RuntimeError("A recording is already in progress.")
        if mode not in session.MODES:
            raise ValueError("Unknown recording mode")
        self.root.mkdir(parents=True, exist_ok=True)
        sid = str(now_ms())
        d = session.session_dir(self.root, sid)
        d.mkdir(parents=True)
        try:
            src: _Source = FakeSource(self) if os.environ.get("MDC_FAKE_MIC") == "1" else QtSource(device, self)
            device_name = "Fake microphone" if isinstance(src, FakeSource) else src.name  # type: ignore[attr-defined]
            (d / "chunks").mkdir()
            self._wav = wav.WavWriter(d / "audio.wav")
            src.pcm.connect(self._on_pcm)
            src.failed.connect(self._on_failed)
            self._chunker, self._paused, self._level, self._peak, self._silent_warned = Chunker(), False, 0.0, 0, False
            self.meta = SessionMeta(sid, (title.strip() or "Untitled recording")[:120], mode, now_ms(), 0, "recording", device_name, 0)
            session.write_meta(d, self.meta)
            self._dir, self._src = d, src
            src.start()
        except Exception:
            self._cleanup_failed(d)
            raise
        self._tick.start()
        self.state_changed.emit("recording")
        return self.meta

    def _cleanup_failed(self, d: Path) -> None:
        if self._wav:
            self._wav.abandon()
        self._wav = self._src = self.meta = self._dir = None
        session.delete_session(self.root, d.name)

    def pause(self) -> None:
        if not self.active or self._paused:
            return
        self._paused = True
        self._src.pause()
        self._flush_chunk()
        self._level = 0.0
        self.meta.status = "paused"
        session.write_meta(self._dir, self.meta)
        self.state_changed.emit("paused")

    def resume(self) -> None:
        if not self.active or not self._paused:
            return
        self._paused = False
        self._src.resume()
        self.meta.status = "recording"
        session.write_meta(self._dir, self.meta)
        self.state_changed.emit("recording")

    def stop(self) -> SessionMeta | None:
        if not self.active:
            return None
        self._tick.stop()
        self._src.stop()
        self._flush_chunk()
        self._wav.finish()
        self._wav = None
        self.meta.duration_ms = self._chunker.active_ms
        self.meta.status = "stopped"
        session.write_meta(self._dir, self.meta)
        meta, self._src = self.meta, None
        self.state_changed.emit("stopped")
        self.finished.emit(meta)
        return meta

    # audio ---------------------------------------------------------------------
    def _on_pcm(self, pcm: bytes) -> None:
        if self._paused or not self.active or not pcm:
            return
        pcm = pcm[: len(pcm) // 2 * 2]
        try:
            self._wav.write(pcm)
        except OSError as e:
            self.notice.emit("error", f"Disk write failed: {e}")
            return
        self._level = self._level * 0.5 + min(rms(pcm), 1.0) * 0.5
        self._peak = max(self._peak, max(map(abs, array.array("h", pcm)), default=0))
        for c in self._chunker.push(pcm):
            self._write_chunk(c)
        # macOS records pure silence when microphone access was denied, without any error
        if not self._silent_warned and self._chunker.active_ms > 3000 and self._peak == 0:
            self._silent_warned = True
            self.notice.emit("warning", "No sound is coming in. Check Microphone access in System Settings → Privacy & Security.")

    def _flush_chunk(self) -> None:
        c = self._chunker.flush()
        if c:
            self._write_chunk(c)

    def _write_chunk(self, c) -> None:
        try:
            (self._dir / "chunks" / f"{c.index:04d}.wav").write_bytes(wav.encode(c.pcm))
        except OSError as e:
            self.notice.emit("error", f"Disk write failed: {e}")
            return
        self.meta.chunks = max(self.meta.chunks, c.index + 1)
        self.chunk_ready.emit(c.index, c.start_ms, c.end_ms, c.voiced_ms)

    def _on_failed(self, why: str) -> None:
        """Device lost: keep everything recorded so far and stay paused so the user can resume."""
        if not self.active or self._paused:
            return
        self._paused = True
        self._flush_chunk()
        self._level = 0.0
        self.meta.status = "paused"
        session.write_meta(self._dir, self.meta)
        self.state_changed.emit("paused")
        self.notice.emit("error", f"The microphone stopped ({why}). Your audio so far is saved; resume to continue.")

    def _emit_level(self) -> None:
        if self.active:
            self.level.emit(self._level, self._chunker.active_ms, self._paused)
            if self._wav and int(time.time()) % 2 == 0:
                self._wav.flush()

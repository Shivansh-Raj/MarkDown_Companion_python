"""Incremental 16 kHz mono 16-bit PCM WAV writer that survives crashes.

The header is written first with a placeholder length, samples are appended as they arrive, and the header is patched
on `finish`. After a crash `repair` fixes the header from the file size, so the audio captured so far is never lost.
"""
from __future__ import annotations

import struct
from pathlib import Path

SAMPLE_RATE = 16_000
HEADER_LEN = 44


def header(data_len: int) -> bytes:
    n = min(data_len, 0xFFFFFFFF)
    return (b"RIFF" + struct.pack("<I", min(36 + n, 0xFFFFFFFF)) + b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, SAMPLE_RATE, SAMPLE_RATE * 2, 2, 16)
            + b"data" + struct.pack("<I", n))


def encode(pcm: bytes) -> bytes:
    """A complete WAV file in memory (used for chunks sent to the transcriber). `pcm` is little-endian int16."""
    return header(len(pcm)) + pcm


class WavWriter:
    def __init__(self, path: Path):
        self._f = open(path, "xb")           # never overwrite an existing recording
        self._f.write(header(0))
        self._len = 0

    def write(self, pcm: bytes) -> None:
        self._f.write(pcm)
        self._len += len(pcm)

    def flush(self) -> None:
        self._f.flush()

    def finish(self) -> None:
        self._f.seek(0)
        self._f.write(header(self._len))
        self._f.close()

    def abandon(self) -> None:
        """Close without patching (the file stays repairable)."""
        try:
            self._f.close()
        except OSError:
            pass


def repair(path: Path) -> int:
    """Fix the header of a WAV left behind by a crash. Returns the recovered duration in milliseconds."""
    size = path.stat().st_size
    data = max(size - HEADER_LEN, 0) & ~1     # whole samples only
    with open(path, "r+b") as f:
        f.seek(0)
        f.write(header(data))
    return data // 2 * 1000 // SAMPLE_RATE

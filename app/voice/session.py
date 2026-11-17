"""On-disk voice sessions: <data dir>/voice/<id>/{session.json, audio.wav, chunks/NNNN.wav, transcript.json, analyses/}."""
from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from . import wav

MODES = ("meeting", "practice", "note")
_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_KIND = re.compile(r"^[a-z-]{1,24}$")


@dataclass
class SessionMeta:
    id: str
    title: str
    mode: str                              # "meeting" | "practice" | "note"
    created_at_ms: int
    duration_ms: int = 0                   # active recording time only (paused time excluded)
    status: str = "recording"              # "recording" | "paused" | "stopped" | "interrupted"
    device: str = ""
    chunks: int = 0


def is_valid_id(session_id: str) -> bool:
    return bool(_ID.match(session_id))


def session_dir(root: Path, session_id: str) -> Path:
    if not is_valid_id(session_id):
        raise ValueError("Invalid session id")
    return root / session_id


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_meta(directory: Path, meta: SessionMeta) -> None:
    _atomic_write(directory / "session.json", json.dumps(asdict(meta), indent=2))


def read_meta(directory: Path) -> SessionMeta | None:
    try:
        data = json.loads((directory / "session.json").read_text(encoding="utf-8"))
        return SessionMeta(**{k: data[k] for k in SessionMeta.__dataclass_fields__ if k in data})
    except (OSError, ValueError, TypeError, KeyError):
        return None


def list_sessions(root: Path) -> list[SessionMeta]:
    """Newest first."""
    if not root.is_dir():
        return []
    metas = [m for d in root.iterdir() if d.is_dir() and (m := read_meta(d))]
    return sorted(metas, key=lambda m: m.created_at_ms, reverse=True)


def recover_interrupted(root: Path) -> list[str]:
    """After a crash: repair the audio header of sessions left recording/paused and mark them interrupted."""
    fixed: list[str] = []
    if not root.is_dir():
        return fixed
    for d in root.iterdir():
        meta = read_meta(d) if d.is_dir() else None
        if meta is None or meta.status not in ("recording", "paused"):
            continue
        audio = d / "audio.wav"
        if audio.exists():
            try:
                meta.duration_ms = wav.repair(audio)
            except OSError:
                pass
        chunks = d / "chunks"
        if chunks.is_dir():
            meta.chunks = sum(1 for _ in chunks.iterdir())
        meta.status = "interrupted"
        try:
            write_meta(d, meta)
            fixed.append(meta.id)
        except OSError:
            pass
    return fixed


# transcripts and analyses are stored beside the audio and never rewritten by one another --------------------
def save_transcript(root: Path, session_id: str, data: dict) -> None:
    _atomic_write(session_dir(root, session_id) / "transcript.json", json.dumps(data))


def load_transcript(root: Path, session_id: str) -> dict | None:
    try:
        return json.loads((session_dir(root, session_id) / "transcript.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_analysis(root: Path, session_id: str, kind: str, data: dict, now_ms: int) -> None:
    """analyses/<kind>-<time>.json; older results are kept (never overwritten)."""
    if not _KIND.match(kind):
        raise ValueError("Invalid analysis type")
    d = session_dir(root, session_id) / "analyses"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{kind}-{now_ms}.json").write_text(json.dumps(data), encoding="utf-8")


def list_analyses(root: Path, session_id: str) -> list[dict]:
    """All saved analyses of a session, oldest first; damaged files are skipped."""
    d = session_dir(root, session_id) / "analyses"
    out: list[dict] = []
    for p in sorted(d.glob("*.json")) if d.is_dir() else []:
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def delete_audio(root: Path, session_id: str) -> None:
    """Remove the recording and its chunks but keep the transcript and notes (retention control)."""
    d = session_dir(root, session_id)
    (d / "audio.wav").unlink(missing_ok=True)
    shutil.rmtree(d / "chunks", ignore_errors=True)


def delete_session(root: Path, session_id: str) -> None:
    shutil.rmtree(session_dir(root, session_id))

"""Groq Whisper speech-to-text (stdlib only). Separate from chat: whisper models are never used for text analysis."""
from __future__ import annotations

import json
import re
import socket
import urllib.error
import urllib.request
import uuid

from ..ai.groq import API, GroqError, _request
from .transcript import Word

_PREFERRED = [re.compile(p) for p in (r"whisper-large-v3-turbo", r"whisper-large-v3$", r"whisper")]
_cache: dict[str, str] = {}


def pick_stt_model(ids: list[str]) -> str | None:
    for pattern in _PREFERRED:
        for i in ids:
            if pattern.search(i):
                return i
    return None


def parse_whisper(data: dict | None, offset_ms: int) -> tuple[str, list[Word]]:
    """Whisper verbose_json -> (text, words) with times shifted onto the recording timeline."""
    data = data or {}
    words: list[Word] = []
    for w in data.get("words") or []:
        text, start, end = w.get("word"), w.get("start"), w.get("end")
        if isinstance(text, str) and isinstance(start, (int, float)) and isinstance(end, (int, float)) and text.strip():
            words.append(Word(text.strip(), round(start * 1000) + offset_ms, round(end * 1000) + offset_ms))
    return (data.get("text") or "").strip(), words


def resolve_stt_model(api_key: str) -> str:
    if api_key in _cache:
        return _cache[api_key]
    with _request(f"{API}/models", api_key) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    model = pick_stt_model([m["id"] for m in data.get("data", []) if "id" in m])
    if model is None:
        raise GroqError("Your Groq key has no speech-to-text model available.")
    _cache[api_key] = model
    return model


def multipart(fields: dict[str, str], file_name: str, file_bytes: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{file_name}"\r\n'
                 f"Content-Type: audio/wav\r\n\r\n".encode() + file_bytes + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def transcribe(api_key: str, wav_bytes: bytes, offset_ms: int, language: str | None = None) -> tuple[str, list[Word]]:
    model = resolve_stt_model(api_key)
    fields = {"model": model, "response_format": "verbose_json", "timestamp_granularities[]": "word", "temperature": "0"}
    if language:
        fields["language"] = language
    body, ctype = multipart(fields, "chunk.wav", wav_bytes)
    req = urllib.request.Request(f"{API}/audio/transcriptions", data=body, method="POST", headers={
        "Authorization": f"Bearer {api_key}", "Content-Type": ctype, "User-Agent": "markdown-companion"})
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return parse_whisper(json.loads(resp.read().decode("utf-8", "replace")), offset_ms)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8", "replace")).get("error", {}).get("message", "")
        except Exception:
            pass
        if e.code == 401:
            raise GroqError("Groq rejected the API key. Check it in Settings.") from None
        if e.code == 429:
            raise GroqError("Groq rate limit reached; retrying.") from None
        raise GroqError(f"Transcription failed ({e.code}). {detail}".strip()) from None
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError):
        raise GroqError("Can't reach Groq. Check your internet connection and try again.") from None

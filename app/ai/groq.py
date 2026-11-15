"""Groq chat client (OpenAI-compatible API). Qt-free; uses only the standard library.

The model is not hardcoded: the account's model list is queried and the best chat model is picked,
because Groq retires and renames models.
"""
from __future__ import annotations

import json
import re
import socket
import threading
import urllib.error
import urllib.request
from typing import Callable

API = "https://api.groq.com/openai/v1"
TIMEOUT = 60

# Best first. Matched against the models the user's key can actually use.
_PREFERRED = [re.compile(p) for p in (r"llama-3\.3-70b", r"gpt-oss-120b", r"llama-?4", r"llama-3\.1-70b",
                                      r"gpt-oss-20b", r"qwen", r"llama-3\.1-8b")]
_NOT_CHAT = re.compile(r"whisper|guard|tts|playai|orpheus|embed|safeguard|compound", re.I)

_model_cache: dict[str, str] = {}


class GroqError(Exception):
    """A problem with a user-readable message."""


class Cancelled(Exception):
    pass


def pick_model(ids: list[str]) -> str | None:
    chat = [i for i in ids if not _NOT_CHAT.search(i)]
    for pattern in _PREFERRED:
        for i in chat:
            if pattern.search(i):
                return i
    return chat[0] if chat else None


def parse_sse(buffer: str) -> tuple[list[str], str, bool]:
    """Extract content deltas from SSE text. Returns (deltas, leftover partial line, done)."""
    *lines, rest = buffer.split("\n")
    deltas: list[str] = []
    done = False
    for line in lines:
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            done = True
            continue
        try:
            delta = json.loads(data)["choices"][0]["delta"].get("content")
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            continue
        if isinstance(delta, str):
            deltas.append(delta)
    return deltas, rest, done


def _request(url: str, api_key: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if body is not None else "GET", headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "markdown-companion",
    })
    try:
        return urllib.request.urlopen(req, timeout=TIMEOUT)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8", "replace")).get("error", {}).get("message", "")
        except Exception:
            pass
        if e.code == 401:
            raise GroqError("Groq rejected the API key. Check it in Settings.") from None
        if e.code == 429:
            raise GroqError("Groq rate limit reached. Try again in a moment.") from None
        raise GroqError(f"Groq request failed ({e.code}). {detail}".strip()) from None
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError):
        raise GroqError("Can't reach Groq. Check your internet connection and try again.") from None


def resolve_model(api_key: str, refresh: bool = False) -> str:
    if not refresh and api_key in _model_cache:
        return _model_cache[api_key]
    with _request(f"{API}/models", api_key) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    ids = [m["id"] for m in data.get("data", []) if m.get("active", True) and "id" in m]
    model = pick_model(ids)
    if model is None:
        raise GroqError("Your Groq key has no chat models available.")
    _model_cache[api_key] = model
    return model


def test_key(api_key: str) -> str:
    """Verifies the key; returns the model that would be used."""
    return resolve_model(api_key, refresh=True)


def stream_chat(api_key: str, system: str, user: str, on_text: Callable[[str], None],
                cancel: threading.Event | None = None) -> str:
    """Streams a completion, calling on_text(full_text_so_far). Raises Cancelled if `cancel` is set."""
    cancel = cancel or threading.Event()
    model = resolve_model(api_key)
    body = {"model": model, "stream": True, "temperature": 0.3,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    full, buffer = "", ""
    decoder = __import__("codecs").getincrementaldecoder("utf-8")("replace")
    with _request(f"{API}/chat/completions", api_key, body) as resp:
        while True:
            if cancel.is_set():
                raise Cancelled()
            chunk = resp.read1(4096) if hasattr(resp, "read1") else resp.read(4096)
            if not chunk:
                break
            buffer += decoder.decode(chunk)
            deltas, buffer, done = parse_sse(buffer)
            if deltas:
                full += "".join(deltas)
                on_text(full)
            if done:
                break
    return full

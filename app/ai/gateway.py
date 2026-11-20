"""The company AI gateway: an alternative to Groq for chat (standard library only).

Two steps, as in the company's own example: POST {client_id, client_secret} to `<auth url>/generate-token` for a token, then
POST an OpenAI-style request to `<model url>/chat/completions` with `Authorization: Bearer <token>`.

The client id and secret are read from the .env file (never stored in MC's settings or sent anywhere except the auth URL). The
two URLs and the model name come from Settings, with .env values (DAIA_AUTH_URL, DAIA_MODEL_URL, DAIA_MODEL) as defaults, so the
company's internal host names are never part of the app's source. The reply is not streamed: the full text arrives at once.
"""
from __future__ import annotations

import json
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

from ..utils import dotenv
from .groq import Cancelled, GroqError

GATEWAY_KEY = "company-gateway"          # stands in for an API key, so code that only checks "is there a key?" keeps working
DEFAULT_MODEL = "gpt-5.1"
TOKEN_TTL = 600                          # seconds, when the gateway does not say how long a token lasts
TIMEOUT = 120


class GatewayError(GroqError):
    """A problem with a user-readable message."""


@dataclass(frozen=True)
class GatewayConfig:
    auth_url: str
    model_url: str
    model: str
    client_id: str
    client_secret: str
    verify_tls: bool = True

    def problems(self) -> list[str]:
        out = []
        if not self.client_id or not self.client_secret:
            out.append("CLIENT_ID and CLIENT_SECRET in the .env file")
        if not self.auth_url:
            out.append("the authentication URL (Settings, or DAIA_AUTH_URL in .env)")
        if not self.model_url:
            out.append("the model URL (Settings, or DAIA_MODEL_URL in .env)")
        return out


def load_config(settings) -> GatewayConfig:
    env = dotenv.load()
    return GatewayConfig(
        auth_url=(settings.get("gateway_auth_url") or env.get("DAIA_AUTH_URL", "")).strip().rstrip("/"),
        model_url=(settings.get("gateway_model_url") or env.get("DAIA_MODEL_URL", "")).strip().rstrip("/"),
        model=(settings.get("gateway_model") or env.get("DAIA_MODEL", "") or DEFAULT_MODEL).strip(),
        client_id=env.get("CLIENT_ID", "").strip(),
        client_secret=env.get("CLIENT_SECRET", "").strip(),
        verify_tls=bool(settings.get("gateway_verify_tls")),
    )


# (url, json body, headers, verify_tls) -> (status, parsed json or None)
Http = Callable[[str, dict, dict, bool], "tuple[int, dict | None]"]


def _post(url: str, body: dict, headers: dict, verify: bool) -> tuple[int, dict | None]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "markdown-companion", **headers})
    ctx = None if verify else ssl._create_unverified_context()          # only when the user switched certificate checks off
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as resp:
            return resp.status, _json(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, _json(e.read())
    except ssl.SSLError:
        raise GatewayError("The secure connection to the company AI failed its certificate check. If your network re-signs traffic, "
                           "turn off \"Verify the server's certificate\" in Settings (this is less safe).") from None
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
        if isinstance(getattr(e, "reason", None), ssl.SSLError):
            raise GatewayError("The secure connection to the company AI failed its certificate check. If your network re-signs traffic, "
                               "turn off \"Verify the server's certificate\" in Settings (this is less safe).") from None
        raise GatewayError("Can't reach the company AI. Check your network or VPN and the URLs in Settings.") from None


def _json(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return None


class _TokenCache:
    def __init__(self):
        self._lock = threading.Lock()
        self._key: tuple | None = None
        self._token = ""
        self._expires = 0.0

    def get(self, cfg: GatewayConfig, http: Http, force: bool = False) -> str:
        with self._lock:
            key = (cfg.auth_url, cfg.client_id, cfg.client_secret)
            if not force and self._key == key and time.time() < self._expires:
                return self._token
            status, body = http(cfg.auth_url + "/generate-token", {"client_id": cfg.client_id, "client_secret": cfg.client_secret}, {}, cfg.verify_tls)
            token = (body or {}).get("token") if isinstance(body, dict) else None
            if status in (401, 403):
                raise GatewayError("The company AI rejected the client id and secret in the .env file.")
            if status >= 400 or not isinstance(token, str) or not token:
                raise GatewayError(f"The company AI did not give a token ({status}). Check the authentication URL in Settings.")
            ttl = (body or {}).get("expires_in")
            self._key, self._token = key, token
            self._expires = time.time() + (min(float(ttl) - 30, 3600) if isinstance(ttl, (int, float)) and ttl > 60 else TOKEN_TTL)
            return token


_tokens = _TokenCache()
_provider: Callable[[], GatewayConfig] | None = None


def configure(provider: Callable[[], GatewayConfig]) -> None:
    """Tells the module where to read the current configuration (set once by AiServices)."""
    global _provider
    _provider = provider


def current() -> GatewayConfig:
    if _provider is None:
        raise GatewayError("The company AI is not set up.")
    return _provider()


def forget_token() -> None:
    _tokens._key = None


def _detail(body) -> str:
    if isinstance(body, dict):
        err = body.get("error")
        msg = err.get("message") if isinstance(err, dict) else err if isinstance(err, str) else body.get("message")
        return str(msg)[:300] if msg else ""
    return ""


def chat(cfg: GatewayConfig, system: str, user: str, http: Http = _post) -> str:
    miss = cfg.problems()
    if miss:
        raise GatewayError("The company AI is not set up. Missing: " + "; ".join(miss) + ".")
    payload = {"model": cfg.model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    for attempt in (0, 1):
        token = _tokens.get(cfg, http, force=attempt == 1)
        status, body = http(cfg.model_url + "/chat/completions", payload, {"Authorization": f"Bearer {token}"}, cfg.verify_tls)
        if status == 401 and attempt == 0:
            continue                                     # the token expired: get a new one once and retry
        break
    if status == 401:
        raise GatewayError("The company AI rejected the token. Check the client id and secret in the .env file.")
    if status == 413 or status == 429:
        raise GatewayError("The company AI is busy or the request was too large. Try again in a moment, or ask about a smaller part.")
    if status >= 400:
        d = _detail(body)
        raise GatewayError(f"The company AI request failed ({status}). {d}".strip())
    try:
        text = body["choices"][0]["message"]["content"]
    except (TypeError, KeyError, IndexError):
        raise GatewayError("The company AI returned a reply in an unexpected format.") from None
    if not isinstance(text, str):
        raise GatewayError("The company AI returned no text.")
    return text


def stream_chat(system: str, user: str, on_text: Callable[[str], None], cancel: threading.Event | None = None, http: Http = _post) -> str:
    """Same shape as Groq's streaming call; the whole reply arrives at once, so `on_text` is called once with it."""
    cancel = cancel or threading.Event()
    if cancel.is_set():
        raise Cancelled()
    text = chat(current(), system, user, http)
    if cancel.is_set():
        raise Cancelled()
    on_text(text)
    return text


def test_connection(cfg: GatewayConfig, http: Http = _post) -> str:
    """Gets a token and sends a one-word request. Returns the model name that answered."""
    chat(cfg, "Reply with the single word OK.", "Hello", http)
    return cfg.model

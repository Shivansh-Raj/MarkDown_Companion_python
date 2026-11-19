"""A small client for the official Notion REST API. Standard library only.

Rate limits (429/529) are retried using `Retry-After`; errors become plain messages; nothing here ever sends
`allow_deleting_content`. Network access goes through an `Http` object so tests use a fake.
"""
from __future__ import annotations

import json
import re
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

API_VERSION = "2026-03-11"
BASE = "https://api.notion.com"
MAX_ATTEMPTS = 3
MAX_WAIT_SECS = 30           # Notion says a connection-level Retry-After is at most 60 seconds
MAX_MARKDOWN_BYTES = 400_000  # Notion rejects requests over 500 KB; stay under it so the error is ours and clear
MAX_TITLE_CHARS = 2_000


class NotionError(Exception):
    """A problem with a user-readable message."""


@dataclass
class Response:
    status: int
    body: Any
    retry_after: int | None = None


class Http(Protocol):
    def send(self, token: str, method: str, path: str, body: Any | None) -> Response: ...


class Network:
    """The real network."""

    def send(self, token: str, method: str, path: str, body: Any | None) -> Response:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(BASE + path, data=data, method=method, headers={
            "Authorization": f"Bearer {token}", "Notion-Version": API_VERSION,
            "Content-Type": "application/json", "User-Agent": "markdown-companion"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return Response(resp.status, _json(resp.read()))
        except urllib.error.HTTPError as e:
            retry = e.headers.get("Retry-After") if e.headers else None
            return Response(e.code, _json(e.read()), int(retry) if retry and retry.strip().isdigit() else None)
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError):
            raise NotionError("Could not reach Notion. Check your internet connection.") from None


def _json(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return None


def describe(status: int, body: Any) -> str:
    """A plain-language message for an error response. Notion's own message is kept when it adds something."""
    api = (body.get("message") if isinstance(body, dict) else "") or ""
    api = str(api).strip()
    base = {
        401: "Notion rejected the token. It may be wrong or revoked.",
        403: "The Notion connection does not have permission for that. Check its capabilities in Notion.",
        404: "Notion could not find that page, or it has not been shared with the connection.",
        400: "Notion refused the request.",
    }.get(status)
    if base is None:
        if status in (429, 529):
            base = "Notion is busy and asked us to slow down. Try again in a moment."
        elif status >= 500:
            base = "Notion had a problem on its side. Try again in a moment."
        else:
            base = f"Notion returned an unexpected error ({status})."
    return base if not api or status in (401, 403, 404) else f"{base} ({api})"


_ID = re.compile(r"[0-9a-fA-F]{32}")


def parse_page_id(text: str) -> str | None:
    """A page id from a pasted Notion link or a bare id (dashes optional), in dashed form. Notion links end with the id."""
    s = text.strip().split("?")[0].split("#")[0]
    tail = s.rsplit("/", 1)[-1].replace("-", "")
    if len(tail) < 32:
        return None
    cand = tail[-32:]
    if not _ID.fullmatch(cand):
        return None
    c = cand.lower()
    return f"{c[0:8]}-{c[8:12]}-{c[12:16]}-{c[16:20]}-{c[20:32]}"


def clean_token(raw: str) -> str:
    t = raw.strip()
    if not t:
        raise NotionError("Paste the connection token from Notion.")
    if len(t) > 500 or any(ch.isspace() for ch in t):
        raise NotionError("That does not look like a Notion token.")
    return t


@dataclass
class PageHit:
    id: str
    title: str
    url: str = ""


@dataclass
class PageMarkdown:
    markdown: str
    truncated: bool = False
    unknown_block_ids: list[str] = field(default_factory=list)


@dataclass
class Updated:
    markdown: str
    truncated: bool
    warnings: list[str]


@dataclass
class Created:
    id: str
    url: str = ""


def page_hit(v: dict) -> PageHit | None:
    if v.get("object") != "page" or not isinstance(v.get("id"), str):
        return None
    title = ""
    for p in (v.get("properties") or {}).values():
        if isinstance(p, dict) and p.get("type") == "title":
            title = "".join(t.get("plain_text", "") for t in p.get("title", []) if isinstance(t, dict))
            break
    return PageHit(v["id"], title if title.strip() else "Untitled", v.get("url") or "")


class Client:
    def __init__(self, http: Http, token: str, sleep: Callable[[float], None] = time.sleep):
        self.http, self.token, self._sleep = http, token, sleep

    def call(self, method: str, path: str, body: Any | None = None) -> Any:
        """Sends a request, retrying rate limits and brief server trouble. Returns the body of a 2xx response."""
        attempt = 0
        while True:
            attempt += 1
            r = self.http.send(self.token, method, path, body)
            if 200 <= r.status < 300:
                return r.body if r.body is not None else {}
            wait = None
            if r.status in (429, 529):
                wait = min(max(r.retry_after if r.retry_after is not None else 1, 1), MAX_WAIT_SECS)
            elif r.status == 503:
                wait = attempt
            if wait is None or attempt >= MAX_ATTEMPTS:
                raise NotionError(describe(r.status, r.body))
            self._sleep(wait)

    # -- connection ------------------------------------------------------------------
    def me(self) -> tuple[str, str]:
        v = self.call("GET", "/v1/users/me")
        return v.get("name") or "", (v.get("bot") or {}).get("workspace_name") or ""

    # -- pages -------------------------------------------------------------------------
    def search_pages(self, query: str) -> list[PageHit]:
        """Pages the connection can see, by title. An empty query lists what it can see."""
        body: dict = {"filter": {"property": "object", "value": "page"}, "page_size": 20}
        if query.strip():
            body["query"] = query.strip()
        v = self.call("POST", "/v1/search", body)
        return [h for h in (page_hit(r) for r in v.get("results", []) if isinstance(r, dict)) if h]

    def create_page(self, parent_page_id: str, title: str, markdown: str) -> Created:
        title = title.strip()
        if not title:
            raise NotionError("Give the page a title.")
        if len(title) > MAX_TITLE_CHARS:
            raise NotionError("That title is too long for Notion.")
        if len(markdown.encode()) > MAX_MARKDOWN_BYTES:
            raise NotionError("That note is too large to send to Notion in one go.")
        pid = parse_page_id(parent_page_id)
        if not pid:
            raise NotionError("That is not a Notion page link or id.")
        v = self.call("POST", "/v1/pages", {
            "parent": {"page_id": pid},
            "properties": {"title": {"title": [{"text": {"content": title}}]}},
            "markdown": markdown, "markdown_version": "v2"})
        return Created(v.get("id") or "", v.get("url") or "")

    def create_empty_page(self, parent_page_id: str, title: str) -> Created:
        pid = parse_page_id(parent_page_id)
        if not pid:
            raise NotionError("That is not a Notion page link or id.")
        v = self.call("POST", "/v1/pages", {"parent": {"page_id": pid},
                                            "properties": {"title": {"title": [{"text": {"content": title.strip()}}]}}})
        return Created(v.get("id") or "", v.get("url") or "")

    def read_page(self, page: str) -> PageMarkdown:
        pid = self._id(page)
        v = self.call("GET", f"/v1/pages/{pid}/markdown?markdown_version=v2")
        return PageMarkdown(v.get("markdown") or "", bool(v.get("truncated")),
                            [x for x in v.get("unknown_block_ids", []) if isinstance(x, str)])

    def page_info(self, page: str) -> PageHit:
        v = self.call("GET", f"/v1/pages/{self._id(page)}")
        hit = page_hit(v) if isinstance(v, dict) else None
        if hit is None:
            raise NotionError("Notion did not return a page.")
        return hit

    def page_exists(self, page: str) -> bool:
        """False when the page was deleted. A page in the trash still answers, so that flag is checked too."""
        try:
            v = self.call("GET", f"/v1/pages/{self._id(page)}")
        except NotionError as e:
            if "could not find" in str(e):
                return False
            raise
        return not (v.get("in_trash") or v.get("archived"))

    def update_content(self, page: str, old_str: str, new_str: str) -> Updated:
        """One targeted search-and-replace edit. `old_str` must match exactly one place. Never allows deleting child pages."""
        v = self.call("PATCH", f"/v1/pages/{self._id(page)}/markdown", {
            "type": "update_content",
            "update_content": {"content_updates": [{"old_str": old_str, "new_str": new_str}]},
            "markdown_version": "v2"})
        warnings = []
        for w in v.get("warnings", []) or []:
            warnings.append(w if isinstance(w, str) else (w.get("message") if isinstance(w, dict) and w.get("message") else json.dumps(w)))
        return Updated(v.get("markdown") or "", bool(v.get("truncated")), warnings)

    def query_data_source(self, ds: str, flt: dict | None, cursor: str | None):
        """One page of rows. Returns (rows, next cursor or None, whether Notion says the result is incomplete)."""
        pid = parse_page_id(ds)
        if not pid:
            raise NotionError("That is not a Notion database link or id.")
        body: dict = {"page_size": 100}
        if flt:
            body["filter"] = flt
        if cursor:
            body["start_cursor"] = cursor
        v = self.call("POST", f"/v1/data_sources/{pid}/query", body)
        nxt = v.get("next_cursor") if v.get("has_more") else None
        return ([r for r in v.get("results", []) if isinstance(r, dict)], nxt,
                (v.get("request_status") or {}).get("type") == "incomplete")

    def get_page(self, page: str) -> dict:
        """The whole page object (properties, parent, trash flag)."""
        return self.call("GET", f"/v1/pages/{self._id(page)}")

    def update_properties(self, page: str, properties: dict) -> None:
        """Changes only the properties given; every other property and the page's content are left alone."""
        self.call("PATCH", f"/v1/pages/{self._id(page)}", {"properties": properties})

    def _children(self, parent: str):
        """Direct children of a page (up to 300)."""
        pid = self._id(parent)
        cursor = None
        for _ in range(3):
            path = f"/v1/blocks/{pid}/children?page_size=100" + (f"&start_cursor={cursor}" if cursor else "")
            v = self.call("GET", path)
            yield from (b for b in v.get("results", []) if isinstance(b, dict))
            cursor = v.get("next_cursor")
            if not v.get("has_more") or not cursor:
                return

    def find_child_page(self, parent: str, title: str) -> str | None:
        for b in self._children(parent):
            if b.get("type") == "child_page" and (b.get("child_page") or {}).get("title") == title:
                return b.get("id")
        return None

    def child_database_titles(self, parent: str) -> list[str]:
        return [(b.get("child_database") or {}).get("title") for b in self._children(parent)
                if b.get("type") == "child_database" and (b.get("child_database") or {}).get("title")]

    # -- databases ----------------------------------------------------------------------
    def search_data_sources(self, query: str) -> list[PageHit]:
        from .db import schema_from
        body: dict = {"filter": {"property": "object", "value": "data_source"}, "page_size": 20}
        if query.strip():
            body["query"] = query.strip()
        v = self.call("POST", "/v1/search", body)
        out = []
        for r in v.get("results", []):
            if isinstance(r, dict) and r.get("object") == "data_source":
                s = schema_from(r)
                if s:
                    out.append(PageHit(s.id, s.title, r.get("url") or ""))
        return out

    def data_source_schema(self, ds: str):
        from .db import schema_from
        pid = parse_page_id(ds)
        if not pid:
            raise NotionError("That is not a Notion database link or id.")
        s = schema_from(self.call("GET", f"/v1/data_sources/{pid}"))
        if s is None:
            raise NotionError("Notion did not return a database.")
        return s

    def create_row(self, ds: str, properties: dict, markdown: str) -> Created:
        pid = parse_page_id(ds)
        if not pid:
            raise NotionError("That is not a Notion database link or id.")
        if len(markdown.encode()) > MAX_MARKDOWN_BYTES:
            raise NotionError("That is too large to send to Notion in one go.")
        body: dict = {"parent": {"type": "data_source_id", "data_source_id": pid}, "properties": properties}
        if markdown.strip():
            body["markdown"], body["markdown_version"] = markdown, "v2"
        v = self.call("POST", "/v1/pages", body)
        return Created(v.get("id") or "", v.get("url") or "")

    def create_database(self, parent_page_id: str, title: str, properties: dict) -> str:
        """Creates an inline database under a page and returns its data source id. Only called on a button press."""
        parent = parse_page_id(parent_page_id)
        if not parent:
            raise NotionError("That is not a Notion page link or id.")
        v = self.call("POST", "/v1/databases", {
            "parent": {"type": "page_id", "page_id": parent},
            "title": [{"type": "text", "text": {"content": title}}],
            "is_inline": True, "initial_data_source": {"properties": properties}})

        def first(x):
            ds = x.get("data_sources") if isinstance(x, dict) else None
            return ds[0].get("id") if ds and isinstance(ds[0], dict) else None
        if first(v):
            return first(v)
        db_id = v.get("id")
        if not db_id:
            raise NotionError("Notion did not return the new database.")
        got = first(self.call("GET", f"/v1/databases/{db_id}"))
        if not got:
            raise NotionError("The database was created, but Notion did not say which data source it has. Choose it with Choose existing.")
        return got

    @staticmethod
    def _id(page: str) -> str:
        pid = parse_page_id(page)
        if not pid:
            raise NotionError("That is not a Notion page link or id.")
        return pid

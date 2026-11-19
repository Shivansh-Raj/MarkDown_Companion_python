"""Asking across a Notion database (port of V1's askdb.rs): read only the few rows most likely to answer a question.

Notion's search only matches titles, so rows are ranked by how many of the question's words their title shares, then by how
recently they were edited. At most MAX_PAGES pages are read; nothing else in the workspace is touched.
"""
from __future__ import annotations

import re

from .client import Client, NotionError, page_hit

MAX_PAGES = 6
_FILLER = set("the and what this that with for from about who how why when where which does did are was were has have any all our there summarise summarize tell list give".split())


def strip_unknown(md: str) -> str:
    """<unknown .../> tags stand for blocks Markdown cannot show; they carry links, not words, so they are not sent to the AI."""
    return re.sub(r"<unknown[^>]*/>|<unknown[^>]*$", "", md)


def rank(rows: list[dict], question: str) -> list[int]:
    words = sorted({w for w in re.findall(r"\w+", question.lower()) if len(w) >= 3 and w not in _FILLER})
    scored = []
    for i, r in enumerate(rows):
        if r.get("in_trash"):
            continue
        hit = page_hit(r)
        t = (hit.title if hit else "").lower()
        scored.append((i, sum(1 for w in words if w in t)))
    scored.sort(key=lambda x: (-x[1], x[0]))
    return [i for i, _ in scored[:MAX_PAGES]]


def pages_for(client: Client, data_source: str, question: str) -> dict:
    schema = client.data_source_schema(data_source)
    rows = client.query_recent(schema.id)
    pages = []
    for i in rank(rows, question):
        r = rows[i]
        try:
            md = strip_unknown(client.read_page(r["id"]).markdown)
        except (NotionError, KeyError):
            continue                       # a page that cannot be read is skipped; the others still answer
        if md.strip():
            hit = page_hit(r)
            pages.append({"id": r["id"], "title": hit.title if hit else "Untitled", "url": r.get("url") or "", "markdown": md})
    return {"database": schema.title, "rows": len(rows), "pages": pages}

"""Prepares a local note for a new Notion page (Qt-free). Nothing is dropped silently: frontmatter becomes a visible `yaml`
code block (property mapping comes later), and the notes list what Notion will show differently."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_FRONT = re.compile(r"^---[ \t]*\r?\n([\s\S]*?)\r?\n(?:---|\.\.\.)[ \t]*(?:\r?\n|$)")


@dataclass
class Draft:
    title: str
    markdown: str
    notes: list[str] = field(default_factory=list)


def prepare_for_notion(source: str, fallback_title: str) -> Draft:
    text = source.lstrip("﻿")
    notes: list[str] = []
    body = text
    title_from_yaml = None
    m = _FRONT.match(text)
    if m:
        yaml = m.group(1)
        t = re.search(r"^title:\s*(.+)$", yaml, re.M)
        title_from_yaml = re.sub(r"^[\"']|[\"']$", "", t.group(1).strip()) if t else None
        body = "```yaml\n" + yaml.rstrip() + "\n```\n\n" + text[m.end():].lstrip()
        notes.append("The frontmatter is kept as a code block at the top. It does not become Notion properties yet.")
    scan = re.sub(r"^```yaml[\s\S]*?```\n\n", "", body, count=1) if m else body
    h = re.search(r"^#\s+(.+?)\s*$", scan, re.M)
    title = title_from_yaml or (re.sub(r"[#*_`]", "", h.group(1)).strip() if h else "") or fallback_title
    links = len(re.findall(r"(?<!!)\[\[[^\]\n]+\]\]", body))
    if links:
        notes.append(f"{links} [[wikilink]]{'' if links == 1 else 's'} will show as plain text. Notion has no equivalent.")
    if re.search(r"!\[\[[^\]\n]+\]\]", body):
        notes.append("Embedded notes (![[...]]) will show as plain text.")
    if re.search(r"^> \[![\w-]+\]", body, re.M):
        notes.append("Callouts will show as quotes.")
    return Draft(title, body, notes)

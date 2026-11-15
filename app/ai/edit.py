"""AI editing of an existing Markdown document: scopes, prompts, safe splicing, progress preservation.

Guarantees (all covered by tests):
  * text outside the chosen scope is byte-identical afterwards;
  * checkbox states inside the scope are restored by task text, so progress is never lost;
  * tasks the model invents always start unchecked.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from markdown_it import MarkdownIt

from ..markdown.checkbox_handler import TASK_RE, split_lines_keepends
from .create import strip_fence

MAX_EDIT_CHARS = 60_000
_md = MarkdownIt("commonmark")


@dataclass(frozen=True)
class Section:
    title: str
    depth: int
    start: int
    end: int


@dataclass(frozen=True)
class Scope:
    kind: str            # "whole" | "section" | "selection"
    start: int = 0
    end: int = 0

    @staticmethod
    def whole() -> "Scope":
        return Scope("whole")

    @staticmethod
    def section(s: Section) -> "Scope":
        return Scope("section", s.start, s.end)

    @staticmethod
    def selection(start: int, end: int) -> "Scope":
        return Scope("selection", start, end)


def system_prompt(kind: str) -> str:
    common = ("You edit Markdown files for a checklist app. Follow the user's instruction exactly and change nothing else. "
              "Preserve every task checkbox state ('- [x]' stays checked, '- [ ]' stays unchecked) unless the instruction "
              "says otherwise. Keep existing wording, order and formatting of anything the instruction does not touch. "
              "Output ONLY the resulting Markdown, with no commentary and no code fences around it.")
    if kind == "selection":
        return (common + " You are given a FRAGMENT of a larger document that the user highlighted. Return only the edited "
                "fragment, to be pasted back in the same place. Do not add a heading or any text the instruction does not require.")
    if kind == "section":
        return (common + " You are given ONE section of a larger document, starting with its heading line. Return that "
                "complete section (keep its heading line unless told to change it) and nothing outside it.")
    return common + " You are given the whole document. Return the complete updated document."


def user_prompt(instruction: str, text: str) -> str:
    return f"Instruction:\n{instruction.strip()}\n\nMarkdown to edit:\n<<<\n{text}\n>>>"


def _line_offsets(source: str) -> list[int]:
    offsets, pos = [], 0
    for line in split_lines_keepends(source):
        offsets.append(pos)
        pos += len(line)
    offsets.append(pos)
    return offsets


def list_sections(source: str) -> list[Section]:
    """Heading sections (each includes its sub-sections). Headings inside code fences are ignored."""
    offsets = _line_offsets(source)
    heads: list[tuple[str, int, int]] = []
    tokens = _md.parse(source)
    for i, t in enumerate(tokens):
        if t.type == "heading_open" and t.map:
            title = tokens[i + 1].content.strip() if i + 1 < len(tokens) else ""
            heads.append((title, int(t.tag[1]), offsets[min(t.map[0], len(offsets) - 1)]))
    out = []
    for i, (title, depth, start) in enumerate(heads):
        end = next((s for _t, d, s in heads[i + 1:] if d <= depth), len(source))
        out.append(Section(title, depth, start, end))
    return out


def _range(source: str, scope: Scope) -> tuple[int, int]:
    return (0, len(source)) if scope.kind == "whole" else (scope.start, scope.end)


def scope_text(source: str, scope: Scope) -> str:
    start, end = _range(source, scope)
    return source[start:end]


def _tasks(text: str) -> list[tuple[int, bool, str]]:
    """(line index, checked, task text) for every task line outside code fences."""
    out, fence = [], None
    for i, line in enumerate(split_lines_keepends(text)):
        m = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if m:
            marker = m.group(1)[0]
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            continue
        if fence:
            continue
        t = TASK_RE.match(line)
        if t:
            out.append((i, t.group(2) in "xX", line[t.end():].strip()))
    return out


def preserve_progress(original: str, edited: str) -> str:
    """Copy checkbox states from `original` onto `edited`.

    Tasks match by text; if some are left over but pair up one-to-one (a task was reworded) they match
    by position. Brand-new tasks start unchecked.
    """
    old, new = _tasks(original), _tasks(edited)
    pool: dict[str, list[int]] = {}
    for idx, (_l, _c, text) in enumerate(old):
        pool.setdefault(text, []).append(idx)
    state: list[bool | None] = [None] * len(new)
    used: set[int] = set()
    for i, (_l, _c, text) in enumerate(new):
        if pool.get(text):
            j = pool[text].pop(0)
            state[i] = old[j][1]
            used.add(j)
    free_new = [i for i, s in enumerate(state) if s is None]
    free_old = [j for j in range(len(old)) if j not in used]
    if free_new and len(free_new) == len(free_old):
        for i, j in zip(free_new, free_old):
            state[i] = old[j][1]
    lines = split_lines_keepends(edited)
    for (line_no, checked, _t), want in zip(new, state):
        want = bool(want)
        if checked != want:
            m = TASK_RE.match(lines[line_no])
            lines[line_no] = lines[line_no][: m.start(2)] + ("x" if want else " ") + lines[line_no][m.end(2):]
    return "".join(lines)


def _clean(ai_output: str) -> str:
    out = strip_fence(ai_output)
    out = re.sub(r"^\s*<<<\s*\n", "", out)
    return re.sub(r"\n\s*>>>\s*$", "", out)


def build_edited_document(source: str, scope: Scope, ai_output: str) -> str:
    """Model output -> new document. Only the scope changes; progress inside it is retained."""
    start, end = _range(source, scope)
    original = source[start:end]
    region = _clean(ai_output)
    if scope.kind == "whole":
        if original.endswith("\n") and not region.endswith("\n"):
            region += "\n"
    else:
        lead = re.match(r"\s*", original).group(0)
        trail = re.search(r"\s*$", original).group(0)
        region = lead + region.strip() + (trail if original.strip() else "")
    region = preserve_progress(original, region)
    return source[:start] + region + source[end:]


def locate_selection(source: str, selected: str) -> tuple[int, int] | None:
    """Map text highlighted in the rendered view back to the source, only if it occurs exactly once."""
    needle = selected.strip()
    if len(needle) < 2:
        return None
    first = source.find(needle)
    if first < 0 or source.find(needle, first + 1) >= 0:
        return None
    return first, first + len(needle)


def utf16_to_index(text: str, units: int) -> int:
    """Qt text positions count UTF-16 units; convert to a Python string index."""
    count = 0
    for i, ch in enumerate(text):
        if count >= units:
            return i
        count += 2 if ord(ch) > 0xFFFF else 1
    return len(text)

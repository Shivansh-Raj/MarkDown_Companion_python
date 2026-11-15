"""Markdown -> HTML body using markdown-it-py.

Raw HTML in documents is escaped (html=False) - the viewer never executes content
from a .md file. Task items become <a href="https://mdc.invalid/toggle/<line>/<0|1>"> links that
the window intercepts; <line> is the 0-based source line, so a click maps exactly
onto one line of the file.
"""
from __future__ import annotations

import html
import re

from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.anchors import anchors_plugin
from mdit_py_plugins.dollarmath import dollarmath_plugin
from pygments import highlight as pyg_highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name
from pygments.util import ClassNotFound

from .checkbox_handler import TASK_RE  # noqa: F401  (re-exported for convenience)

_TASK_PREFIX = re.compile(r"^\[([ xX])\](?:\s+|$)")
_FORMATTER = HtmlFormatter(nowrap=True)


def pygments_css(style: str = "native") -> str:
    return HtmlFormatter(style=style, cssclass="codehilite").get_style_defs(".codehilite")


def _highlight(code: str, lang: str, _attrs: str) -> str:
    try:
        lexer = get_lexer_by_name(lang) if lang else TextLexer()
    except ClassNotFound:
        lexer = TextLexer()
    return '<pre class="codehilite"><code>' + pyg_highlight(code, lexer, _FORMATTER) + "</code></pre>\n"


def _tasklist_rule(state) -> None:
    """Core rule: turn `[ ]`/`[x]` at the start of a list item into a checkbox link."""
    tokens = state.tokens
    for i in range(len(tokens) - 2):
        item = tokens[i]
        if item.type != "list_item_open" or item.map is None:
            continue
        inline = tokens[i + 2]
        if tokens[i + 1].type != "paragraph_open" or inline.type != "inline" or not inline.children:
            continue
        first = inline.children[0]
        m = _TASK_PREFIX.match(first.content) if first.type == "text" else None
        if not m:
            continue
        checked = m.group(1) in "xX"
        first.content = first.content[m.end():]
        box = Token("html_inline", "", 0)
        box.content = (
            f'<a class="cb {"checked" if checked else "open"}" '
            f'href="https://mdc.invalid/toggle/{item.map[0]}/{int(checked)}" '
            f'title="Click to {"uncheck" if checked else "check"}">{"&#9746;" if checked else "&#9744;"}</a> '
        )
        inline.children.insert(0, box)
        item.attrJoin("class", "task-item")
        for j in range(i - 1, -1, -1):  # mark the enclosing list
            if tokens[j].type in ("bullet_list_open", "ordered_list_open") and tokens[j].level == item.level - 1:
                if "task-list" not in (tokens[j].attrGet("class") or ""):
                    tokens[j].attrJoin("class", "task-list")
                break


def _math_inline(self, tokens, idx, options, env):
    return f'<span class="math inline">{html.escape(tokens[idx].content)}</span>'


def _math_block(self, tokens, idx, options, env):
    return f'<div class="math block">{html.escape(tokens[idx].content)}</div>\n'


def _build() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False, "linkify": False, "highlight": _highlight})
    md.enable(["table", "strikethrough"])
    md.use(anchors_plugin, max_level=6, permalink=False)
    md.use(dollarmath_plugin, allow_space=False, allow_digits=False)
    md.add_render_rule("math_inline", _math_inline)
    md.add_render_rule("math_inline_double", _math_block)
    md.add_render_rule("math_block", _math_block)
    md.core.ruler.push("tasklist", _tasklist_rule)
    return md


_MD = _build()


def render_markdown(text: str) -> str:
    """Render to an HTML fragment. Never raises on malformed input."""
    try:
        return _MD.render(text)
    except Exception as exc:  # defensive: show the source instead of crashing
        return (f'<p class="render-error">Could not render this file ({html.escape(str(exc))}).</p>'
                f"<pre>{html.escape(text)}</pre>")

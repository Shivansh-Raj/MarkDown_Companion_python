"""Full-page HTML shell around the rendered Markdown fragment."""
from __future__ import annotations

import html

from .theme import content_css

CONTENT_ID = "content"


def build_page(body_html: str, theme_id: str, title: str = "") -> str:
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<title>{html.escape(title)}</title>"
        f"<style>{content_css(theme_id)}</style></head>"
        f'<body><div id="{CONTENT_ID}">{body_html}</div></body></html>'
    )

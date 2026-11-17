"""Rendering surfaces. WebEngine when available; QTextBrowser as a degraded fallback.

Both expose the same tiny interface:
    set_content(body_html, theme_id, base_dir)  - full (re)load
    update_body(body_html)                      - in-place update that keeps scroll position
Signals: toggle_requested(line, was_checked), file_link_requested(path)
"""
from __future__ import annotations

import json
import os
from urllib.parse import unquote

from PySide6.QtCore import QObject, QTimer, QUrl, Signal, Qt
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import QTextBrowser, QWidget

from ..markdown.html_template import CONTENT_ID, build_page
from ..markdown.theme import load_theme
from ..utils.logging_config import get_logger
from . import scale

log = get_logger("view")
ACTION_HOST = "mdc.invalid"
_MD_EXT = (".md", ".markdown", ".mdown")


def is_action_url(url: QUrl) -> bool:
    """In-app action links use the reserved, never-resolving host mdc.invalid (RFC 2606).

    A custom scheme would be cleaner but Chromium never reports unregistered-scheme
    navigations to Qt, whereas https links are always passed to acceptNavigationRequest.
    """
    return url.host().lower() == ACTION_HOST


def _route_link(url: QUrl, owner) -> bool:
    """Handle a clicked link. Returns True if the navigation was consumed."""
    scheme = url.scheme().lower()
    if is_action_url(url):
        parts = [p for p in url.path().split("/") if p]
        if len(parts) == 3 and parts[0] == "toggle" and all(p.isdigit() for p in parts[1:]):
            if getattr(owner, "toggle_busy", False):
                return True     # a click is still being applied; this link carries stale state
            owner.toggle_requested.emit(int(parts[1]), bool(int(parts[2])))
            if hasattr(owner, "mark_toggle_pending"):
                owner.mark_toggle_pending()
        return True
    if scheme in ("http", "https", "mailto"):
        QDesktopServices.openUrl(url)
        return True
    if scheme == "file":
        path = unquote(url.toLocalFile())
        if path.lower().endswith(_MD_EXT):
            owner.file_link_requested.emit(path)
        else:
            QDesktopServices.openUrl(url)
        return True
    return False


class FallbackMarkdownView(QTextBrowser):
    toggle_requested = Signal(int, bool)
    file_link_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setOpenLinks(False)
        self.setFrameShape(QTextBrowser.Shape.NoFrame)
        self.anchorClicked.connect(lambda url: _route_link(url, self))
        # the zoom is relative to the platform's own size: the app font is already scaled by the Text size setting
        self._base_pt = scale.base_font().pointSizeF() if scale.base_font().pointSizeF() > 0 else self.font().pointSizeF()

    def set_zoom(self, factor: float) -> None:
        f = self.font()
        f.setPointSizeF(self._base_pt * factor)
        self.setFont(f)
        self.document().setDefaultFont(f)

    def set_content(self, body_html: str, theme_id: str, base_dir: str) -> None:
        t = load_theme(theme_id)
        self.document().setBaseUrl(QUrl.fromLocalFile(base_dir.rstrip("/\\") + "/"))
        self.setStyleSheet(f"QTextBrowser {{ background: {t['bg']}; color: {t['fg']}; }}")
        self.document().setDefaultStyleSheet(
            f"a {{ color: {t['link']}; }} code, pre {{ color: {t['accent2']}; background-color: {t['code_bg']}; }}"
            f" h1, h2 {{ color: {t['accent']}; }} th {{ color: {t['accent']}; }} table {{ border: 1px; }}")
        self.setHtml(body_html)

    def selected_text(self) -> str:
        return self.textCursor().selectedText().replace("\u2029", "\n")

    def update_body(self, body_html: str) -> None:
        bar = self.verticalScrollBar()
        pos = bar.value()
        self.setHtml(body_html)
        bar.setValue(pos)


try:
    from PySide6.QtWebEngineCore import QWebEnginePage
    from PySide6.QtWebEngineWidgets import QWebEngineView


    class _Page(QWebEnginePage):
        def __init__(self, owner, parent=None):
            super().__init__(parent)
            self._owner = owner

        def acceptNavigationRequest(self, url, nav_type, is_main_frame):  # noqa: N802
            if is_action_url(url):         # our own action link, whatever the navigation type
                _route_link(url, self._owner)
                return False
            if nav_type != QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
                return True
            if url.hasFragment() and url.scheme() in ("file", "") and \
                    url.adjusted(QUrl.UrlFormattingOption.RemoveFragment) == self._owner.base_url:
                frag = url.fragment()
                self.runJavaScript(
                    f"var e=document.getElementById({json.dumps(frag)});if(e)e.scrollIntoView();")
                return False
            _route_link(url, self._owner)
            return False

    class WebMarkdownView(QWebEngineView):
        toggle_requested = Signal(int, bool)
        file_link_requested = Signal(str)

        def __init__(self, parent: QWidget | None = None):
            super().__init__(parent)
            self.base_url = QUrl()
            self._loaded = False
            self._pending: str | None = None
            self.toggle_busy = False
            self.setPage(_Page(self, self))
            self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
            self.page().loadFinished.connect(self._on_loaded)

        def set_content(self, body_html: str, theme_id: str, base_dir: str) -> None:
            self._loaded = False
            self._pending = None
            self.base_url = QUrl.fromLocalFile(os.path.join(base_dir, ""))
            self.page().setBackgroundColor(QColor(load_theme(theme_id)["bg"]))
            self.setHtml(build_page(body_html, theme_id), self.base_url)

        def set_zoom(self, factor: float) -> None:
            self.setZoomFactor(factor)

        def selected_text(self) -> str:
            return self.selectedText().replace("\u2029", "\n")

        def update_body(self, body_html: str) -> None:
            if not self._loaded:
                self._pending = body_html
                return
            self._inject(body_html)

        def mark_toggle_pending(self) -> None:
            """Ignore further checkbox clicks until the re-render lands (or 1.5 s pass).

            The page is re-rendered asynchronously, so a fast second click would still carry
            the old checked state and be rejected as a conflict.
            """
            self.toggle_busy = True
            QTimer.singleShot(1500, self._clear_toggle_busy)

        def _clear_toggle_busy(self) -> None:
            self.toggle_busy = False

        def _inject(self, body_html: str) -> None:
            # Swapping innerHTML (not reloading the page) keeps the scroll position natively.
            self.page().runJavaScript(
                f"document.getElementById({json.dumps(CONTENT_ID)}).innerHTML = {json.dumps(body_html)};",
                lambda _r: self._clear_toggle_busy())

        def _on_loaded(self, ok: bool) -> None:
            # Clicking an action link (navigation refused in acceptNavigationRequest) also fires
            # loadFinished(False) on the already-loaded page. That must not mark the page unloaded,
            # or every later refresh is queued forever and the view shows stale checkbox state.
            if ok:
                self._loaded = True
            if ok and self._pending is not None:
                body, self._pending = self._pending, None
                self._inject(body)

    WEBENGINE_AVAILABLE = True
except Exception:  # ImportError, or a broken Qt install
    log.warning("QtWebEngine unavailable; using QTextBrowser fallback", exc_info=True)
    WEBENGINE_AVAILABLE = False


def create_view(parent: QWidget | None = None):
    """Best available view. MDC_NO_WEBENGINE=1 forces the fallback (debugging, headless tests)."""
    if WEBENGINE_AVAILABLE and os.environ.get("MDC_NO_WEBENGINE") != "1":
        try:
            return WebMarkdownView(parent)
        except Exception:
            log.exception("WebEngine view failed to initialise; falling back")
    return FallbackMarkdownView(parent)

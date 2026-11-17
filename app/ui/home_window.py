"""Home screen, styled like V1's launcher: a search box, one clean file list and a small footer. Nothing else."""
from __future__ import annotations

import os

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QFont, QKeySequence, QMouseEvent, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu,
                               QPushButton, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget)

from ..markdown.theme import load_theme
from . import scale
from .markdown_window import CORNER, EDGE
from .pixel_icon import app_icon
from .title_bar import TitleBar
from .typing_cat import TypingCat

MD_EXT = (".md", ".markdown", ".mdown", ".txt")
_PATH, _KEY, _OPEN, _MISSING = (Qt.ItemDataRole.UserRole + n for n in range(4))
_E = Qt.Edge
ROW_H = 46                                              # at 100% text size; sizes below go through scale.px()


class _Search(QLineEdit):
    """Search box that also drives the list from the keyboard (arrows move, Enter opens, Esc hides), like V1."""
    move = Signal(int)
    activate = Signal()
    escape = Signal()

    def keyPressEvent(self, e) -> None:  # noqa: N802
        k = e.key()
        if k == Qt.Key.Key_Down:
            self.move.emit(1)
        elif k == Qt.Key.Key_Up:
            self.move.emit(-1)
        elif k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.activate.emit()
        elif k == Qt.Key.Key_Escape:
            self.escape.emit()
        else:
            super().keyPressEvent(e)


class _RowDelegate(QStyledItemDelegate):
    """V1's `.row`: bold name on the left, an outlined badge on the right, the folder in small muted text below."""

    def __init__(self, colors, parent=None):
        super().__init__(parent)
        self._colors = colors                              # callable returning the current theme

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        return QSize(option.rect.width(), scale.px(ROW_H))

    def paint(self, p: QPainter, option, index) -> None:
        t = self._colors()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        missing = bool(index.data(_MISSING))
        ink = QColor(t["accent_ink"] if selected else t["fg"])
        p.save()
        r = option.rect
        if selected:
            p.fillRect(r, QColor(t["accent"]))
        p.setPen(QPen(QColor(t["muted"]), 1, Qt.PenStyle.DashLine))               # dashed row divider
        p.drawLine(r.left() + 1, r.bottom(), r.right() - 1, r.bottom())
        name = QFont(option.font)
        name.setBold(True)
        name.setPointSizeF(max(9.0 * scale.get(), option.font.pointSizeF() * 1.05))
        right = r.right() - scale.px(10)
        badge = "MISSING" if missing else ("OPEN" if index.data(_OPEN) else "")
        if badge:
            bf = QFont("Consolas, Menlo, monospace")
            bf.setStyleHint(QFont.StyleHint.Monospace)
            bf.setBold(True)
            bf.setPixelSize(scale.px(10))
            p.setFont(bf)
            w = p.fontMetrics().horizontalAdvance(badge) + scale.px(10)
            box = QRect(right - w, r.top() + scale.px(7), w, scale.px(16))
            p.setPen(QPen(ink, 2))
            p.drawRect(box.adjusted(1, 1, -1, -1))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, badge)
            right = box.left() - scale.px(8)
        p.setFont(name)
        p.setPen(QColor(t["muted"] if missing and not selected else ink))
        left, room = r.left() + scale.px(10), max(right - r.left() - scale.px(10), 10)
        p.drawText(QRect(left, r.top() + scale.px(5), room, scale.px(20)), Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(index.data(Qt.ItemDataRole.DisplayRole) or "", Qt.TextElideMode.ElideRight, room))
        small = QFont(option.font)
        small.setPixelSize(scale.px(11))
        p.setFont(small)
        folder = os.path.dirname(index.data(_PATH) or "") or "/"
        ink.setAlphaF(0.75)
        p.setPen(ink)
        p.drawText(QRect(left, r.top() + scale.px(25), r.width() - 2 * scale.px(10), scale.px(16)), Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(folder, Qt.TextElideMode.ElideMiddle, r.width() - 2 * scale.px(10)))
        p.restore()


class HomeWindow(QWidget):
    open_dialog_requested = Signal()
    open_path_requested = Signal(str)
    focus_requested = Signal(str)       # window key
    close_window_requested = Signal(str)
    forget_requested = Signal(str)      # file key
    settings_requested = Signal()
    ai_create_requested = Signal()
    voice_requested = Signal()
    exit_requested = Signal()

    def __init__(self, db, window_manager, settings):
        # Same chrome as the note windows: frameless, always on top, pixel frame, custom title bar.
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self._db, self._wm, self._settings = db, window_manager, settings
        self.shortcut_status = ""
        self.setObjectName("root")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setMouseTracking(True)
        self.setMinimumSize(420, 420)
        self.setWindowTitle("Markdown Companion V2")
        self.setWindowIcon(app_icon())
        self.setAcceptDrops(True)
        self.resize(540, 520)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(EDGE, EDGE, EDGE, EDGE)
        frame = QWidget(self)
        frame.setObjectName("frame")
        frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer.addWidget(frame)
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(2, 2, 2, 2)
        fl.setSpacing(0)
        self.title_bar = TitleBar("MD COMPANION", buttons=("settings", "minimize", "close"))
        self.title_bar.settings_clicked.connect(self.settings_requested)
        self.title_bar.minimize_clicked.connect(self.showMinimized)
        self.title_bar.close_clicked.connect(self.hide)     # the app keeps running in the tray
        fl.addWidget(self.title_bar)
        self.body = QWidget()
        self.body.setObjectName("launcher")
        self.body.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        fl.addWidget(self.body, 1)

        root = QVBoxLayout(self.body)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        self.search = _Search()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("Search Markdown files…")
        self.search.setClearButtonEnabled(False)
        self.search.textChanged.connect(lambda _t: self.refresh(reset=True))
        self.search.move.connect(self._move)
        self.search.activate.connect(self._open_current)
        self.search.escape.connect(self.hide)
        root.addWidget(self.search)

        self.status = QLabel()                               # e.g. a global shortcut that could not be registered
        self.status.setObjectName("launchererror")
        self.status.setWordWrap(True)
        self.status.hide()
        root.addWidget(self.status)

        self.results = QListWidget()
        self.results.setObjectName("results")
        self.results.setMouseTracking(True)
        self.results.setItemDelegate(_RowDelegate(self._theme, self.results))
        self.results.itemEntered.connect(self.results.setCurrentItem)          # hover selects, like V1
        self.results.itemActivated.connect(lambda it: self._open_item(it))
        self.results.itemClicked.connect(lambda it: self._open_item(it))
        self.results.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.results.customContextMenuRequested.connect(self._menu)
        self.results.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(self.results, 1)

        # footer: V1's three buttons; the cat sits on + ADD FILE
        self.open_btn = self._button("+ ADD FILE", primary=True)
        self.open_btn.clicked.connect(self.open_dialog_requested)
        self.ai_btn = self._button("\u270e  NEW WITH AI")
        self.ai_btn.clicked.connect(self.ai_create_requested)
        self.voice_btn = self._button("\u25cf  VOICE")
        self.voice_btn.clicked.connect(self.voice_requested)
        btns = QHBoxLayout()
        btns.setSpacing(8)
        for b in (self.open_btn, self.ai_btn, self.voice_btn):
            btns.addWidget(b)
        btns.addStretch(1)
        root.addLayout(btns)

        self.hint = QLabel()
        self.hint.setObjectName("launcherhint")
        self.hint.setWordWrap(True)
        quit_btn = QPushButton("EXIT APP")
        quit_btn.setProperty("v1", True)
        quit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        quit_btn.setToolTip("Quit the app completely (closing this window keeps it running in the tray)")
        quit_btn.clicked.connect(self.exit_requested)
        self._decorate(quit_btn)
        foot = QHBoxLayout()
        foot.addWidget(self.hint, 1)
        foot.addWidget(quit_btn)
        root.addLayout(foot)

        anim = self._settings.get("animations") is not False
        self.cat = TypingCat(self._settings.get("theme") or "retro", anim, scale=2, crop_top=8, parent=self.body)
        self.cat.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.cat.raise_()
        QShortcut(QKeySequence("Ctrl+,"), self, activated=self.settings_requested.emit)
        self._cat_timer = QTimer(self, singleShot=True, interval=0)       # owned by the window, so it dies with it
        self._cat_timer.timeout.connect(self._place_cat)

        wm_signal = getattr(window_manager, "windows_changed", None)
        if wm_signal is not None:
            wm_signal.connect(self.refresh)
        self.refresh()

    # widgets ---------------------------------------------------------------------------------
    def _theme(self) -> dict:
        return load_theme(self._settings.get("theme") or "retro")

    def _button(self, text: str, primary: bool = False) -> QPushButton:
        b = QPushButton(text)
        b.setProperty("v1", True)
        b.setProperty("primary", primary)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setMinimumHeight(scale.px(32))
        b.setMinimumWidth(scale.px(len(text) * 8 + 24))     # asymmetric borders can make Qt's size hint too small
        return self._decorate(b)

    def _decorate(self, b: QPushButton) -> QPushButton:
        fx = QGraphicsDropShadowEffect(b)                    # V1's `box-shadow: 2px 2px 0 var(--shadow)`
        fx.setBlurRadius(0)
        fx.setOffset(2, 2)
        fx.setColor(QColor(self._theme()["shadow"]))
        b.setGraphicsEffect(fx)
        return b

    def apply_scale(self) -> None:
        """The Text size setting changed: rows, buttons and the cat position follow it."""
        for b in self.findChildren(QPushButton):
            if b.property("v1"):
                b.setMinimumHeight(scale.px(32))
                b.setMinimumWidth(scale.px(len(b.text()) * 8 + 24))
        self.title_bar.rescale()
        self.results.scheduleDelayedItemsLayout()
        self.results.viewport().update()
        self._cat_timer.start()                              # after the layout has settled at the new size

    def apply_theme(self) -> None:
        """Called when the theme changes: shadows and the cat follow it, the list repaints."""
        color = QColor(self._theme()["shadow"])
        for b in self.findChildren(QPushButton):
            fx = b.graphicsEffect()
            if isinstance(fx, QGraphicsDropShadowEffect) and b.property("v1"):
                fx.setColor(color)
        self.cat.set_theme(self._settings.get("theme") or "retro")
        self.results.viewport().update()

    def _place_cat(self) -> None:
        """The cat perches on the top-right of the + ADD FILE button (an overlay, so it takes no layout space)."""
        pos = self.open_btn.mapTo(self.body, QPoint(0, 0))
        self.cat.move(pos.x() + max(self.open_btn.width() - self.cat.width() - 8, 4), pos.y() - self.cat.height() + 5)
        self.cat.raise_()

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._place_cat()

    # content -----------------------------------------------------------------------------------
    def set_shortcut_status(self, text: str) -> None:
        self.shortcut_status = text
        self._update_hint()

    def _update_hint(self) -> None:
        seq = self._settings.get("shortcut_primary")
        self.status.setText(f"\u26a0 {self.shortcut_status}" if self.shortcut_status else "")
        self.status.setVisible(bool(self.shortcut_status))
        self.hint.setText("\u2191\u2193 select \u00b7 Enter open \u00b7 Esc close \u00b7 Ctrl+, settings"
                          + (f" \u00b7 {seq} toggles" if seq else ""))

    def refresh(self, reset: bool = False) -> None:
        """Rebuild the list. The highlighted file is kept across refreshes unless `reset` (new search text, window shown)."""
        self._update_hint()
        self.cat.set_theme(self._settings.get("theme") or "retro")
        q = self.search.text().strip().lower()
        open_keys = {w.key for w in self._wm.windows()}
        keep = None if reset or self.results.currentItem() is None else self.results.currentItem().data(_KEY)
        self.results.clear()
        rows = self._db.recent_files(200)
        shown = 0
        for row in rows:
            name = os.path.basename(row["path"])
            if q and q not in name.lower() and q not in row["path"].lower():
                continue
            it = QListWidgetItem(name)
            it.setToolTip(row["path"])
            it.setData(_PATH, row["path"])
            it.setData(_KEY, row["key"])
            it.setData(_OPEN, row["key"] in open_keys)
            it.setData(_MISSING, not os.path.exists(row["path"]))
            self.results.addItem(it)
            shown += 1
        if shown == 0:
            it = QListWidgetItem("No files yet. Open a Markdown file, or drag one onto this window." if not rows else "No matches.")
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.results.addItem(it)
        else:
            row = next((i for i in range(self.results.count()) if self.results.item(i).data(_KEY) == keep), 0)
            self.results.setCurrentRow(row)

    def showEvent(self, e) -> None:  # noqa: N802
        self.refresh(reset=True)
        super().showEvent(e)
        self.search.setFocus()
        self._place_cat()
        self.cat.start()

    def hideEvent(self, e) -> None:  # noqa: N802
        self.cat.stop()
        super().hideEvent(e)

    def present(self) -> None:
        self.show()
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()
        self.search.setFocus()

    # keyboard / activation ---------------------------------------------------------------------
    def _move(self, delta: int) -> None:
        n = self.results.count()
        if n and self.results.item(0).data(_KEY) is not None:
            self.results.setCurrentRow(max(0, min(n - 1, self.results.currentRow() + delta)))

    def _open_current(self) -> None:
        self._open_item(self.results.currentItem())

    def _open_item(self, it) -> None:
        if it is not None and it.data(_PATH):
            self.open_path_requested.emit(it.data(_PATH))      # focuses the window instead if it is already open

    # context menu ----------------------------------------------------------------------------------
    def _menu(self, pos) -> None:
        it = self.results.itemAt(pos)
        if it is None or not it.data(_PATH):
            return
        m = QMenu(self)
        if it.data(_OPEN):
            m.addAction("Show / focus", lambda: self.focus_requested.emit(it.data(_KEY)))
            m.addAction("Close window", lambda: self.close_window_requested.emit(it.data(_KEY)))
        else:
            m.addAction("Open", lambda: self.open_path_requested.emit(it.data(_PATH)))
        m.addAction("Remove from list", lambda: (self.forget_requested.emit(it.data(_KEY)), self.refresh()))
        m.exec(self.results.viewport().mapToGlobal(pos))

    # edge resize (same behaviour as the note windows) -------------------------------
    def _edges_at(self, pos: QPoint) -> Qt.Edge:
        w, h = self.width(), self.height()
        edges = Qt.Edge(0)
        if pos.x() < EDGE or (pos.x() < CORNER and pos.y() > h - EDGE):
            edges |= _E.LeftEdge
        if pos.x() > w - EDGE or (pos.x() > w - CORNER and pos.y() > h - EDGE):
            edges |= _E.RightEdge
        if pos.y() < EDGE:
            edges |= _E.TopEdge
        if pos.y() > h - EDGE:
            edges |= _E.BottomEdge
        return edges

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        edges = self._edges_at(e.position().toPoint())
        horiz, vert = bool(edges & (_E.LeftEdge | _E.RightEdge)), bool(edges & (_E.TopEdge | _E.BottomEdge))
        if horiz and vert:
            fwd = bool(edges & _E.LeftEdge) == bool(edges & _E.TopEdge)
            self.setCursor(Qt.CursorShape.SizeFDiagCursor if fwd else Qt.CursorShape.SizeBDiagCursor)
        elif horiz:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif vert:
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.unsetCursor()

    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        edges = self._edges_at(e.position().toPoint())
        if e.button() == Qt.MouseButton.LeftButton and edges and self.windowHandle() is not None:
            self.windowHandle().startSystemResize(edges)

    # drag & drop ------------------------------------------------------------------
    def dragEnterEvent(self, e: QDragEnterEvent) -> None:  # noqa: N802
        if any(u.isLocalFile() and u.toLocalFile().lower().endswith(MD_EXT) for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e: QDropEvent) -> None:  # noqa: N802
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if u.isLocalFile() and p.lower().endswith(MD_EXT):
                self.open_path_requested.emit(p)
        e.acceptProposedAction()

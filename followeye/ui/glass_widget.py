from __future__ import annotations

import sys

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..config import NotificationSettings, WidgetCorner
from ..skills.skills import SKILL_LABELS, Suggestion

WIDTH = 380
MARGIN = 18
RADIUS = 14
MAX_DETAILS = 5
SKILL_COLORS = {"form_fill": "#7AA2FF", "file_find": "#F5B65C", "context": "#3EC7C2"}

STYLE = """
QWidget#glass { background: transparent; }
QLabel { color: rgba(255,255,255,0.92); background: transparent; }
QLabel#title { font-size: 14px; font-weight: 600; }
QLabel#body { font-size: 12.5px; color: rgba(255,255,255,0.85); }
QLabel#detail { font-size: 11.5px; color: rgba(255,255,255,0.70); }
QLabel#meta, QLabel#hint { font-size: 11px; color: rgba(255,255,255,0.55); }
QLabel#status { font-size: 12.5px; color: #9FF0EC; }
QPushButton {
    color: white; background: rgba(255,255,255,0.10);
    border: 1px solid rgba(255,255,255,0.16); border-radius: 8px; padding: 6px 12px; font-size: 12px;
}
QPushButton:hover { background: rgba(255,255,255,0.18); }
QPushButton#primary { background: rgba(62,199,194,0.85); border-color: rgba(255,255,255,0.25); }
QPushButton#primary:hover { background: rgba(62,199,194,1.0); }
QToolButton { color: rgba(255,255,255,0.7); background: transparent; border: none; font-size: 14px; }
QToolButton:hover { color: white; }
"""


class GlassNotification(QWidget):
    """Frosted-glass card that always appears in the same screen corner, only when there is something to show."""

    action_requested = Signal(object, int)  # suggestion, action index
    closed = Signal(object, str)  # suggestion, "dismissed" | "ignored" | "replaced"
    visibility_changed = Signal(bool)

    def __init__(self, settings: NotificationSettings, accept_hint: str = "", dismiss_hint: str = ""):
        super().__init__(None)
        self.setObjectName("glass")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedWidth(WIDTH)
        self.setStyleSheet(STYLE)
        font = QFont("Segoe UI Variable Text")
        font.setStyleHint(QFont.StyleHint.SansSerif)
        self.setFont(font)

        self.settings = settings
        self.accept_hint = accept_hint
        self.dismiss_hint = dismiss_hint
        self.suggestion: Suggestion | None = None
        self._acrylic = False
        self._native_ready = False
        self._busy = False

        self._hide_timer = QTimer(self, singleShot=True, timeout=self._timed_out)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(180)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.finished.connect(self._fade_finished)
        self._fading_out = False

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(16, 12, 12, 14)
        self._layout.setSpacing(6)
        self._content = QWidget(self)
        self._layout.addWidget(self._content)

    # --- public API --------------------------------------------------------

    def show_suggestion(self, suggestion: Suggestion) -> None:
        if self.suggestion is not None and self.isVisible():
            self.closed.emit(self.suggestion, "replaced")
        self.suggestion = suggestion
        self._busy = False
        self._build_suggestion(suggestion)
        self._present(self.settings.auto_hide_seconds * 1000)
        self.visibility_changed.emit(True)

    def show_info(self, text: str, ms: int = 2200) -> None:
        if self.suggestion is not None and self.isVisible():
            return  # never hide a suggestion behind a status message
        self._build_info(text)
        self._present(ms)

    def show_status(self, text: str) -> None:
        """Result of an action on the current suggestion; closes the card shortly after."""
        self._status.setText(text)
        self._status.show()
        self._hide_timer.start(1800)

    def accept_primary(self) -> None:
        if self.suggestion is not None and self.suggestion.actions and not self._busy:
            self._run_action(0)

    def dismiss(self) -> None:
        if self.suggestion is not None:
            self._finish("dismissed")

    # --- building ------------------------------------------------------------

    def _reset_content(self) -> QVBoxLayout:
        self._content.deleteLater()
        self._content = QWidget(self)
        self._layout.addWidget(self._content)
        layout = QVBoxLayout(self._content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        return layout

    def _build_suggestion(self, s: Suggestion) -> None:
        layout = self._reset_content()
        header = QHBoxLayout()
        color = SKILL_COLORS.get(s.skill.value, "#3EC7C2")
        meta = QLabel(f'<span style="color:{color}">●</span>&nbsp; {SKILL_LABELS.get(s.skill, "")} · {s.source}')
        meta.setObjectName("meta")
        header.addWidget(meta)
        header.addStretch(1)
        close = QToolButton(text="✕", toolTip="Dismiss")
        close.clicked.connect(self.dismiss)
        header.addWidget(close)
        layout.addLayout(header)

        title = QLabel(s.title, objectName="title", wordWrap=True)
        layout.addWidget(title)
        if s.body:
            body = QLabel(s.body, objectName="body", wordWrap=True)
            body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(body)
        for line in s.details[:MAX_DETAILS]:
            detail = QLabel(objectName="detail")
            detail.setText(detail.fontMetrics().elidedText("• " + line, Qt.TextElideMode.ElideMiddle, WIDTH - 40))
            detail.setToolTip(line)
            layout.addWidget(detail)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        for i, action in enumerate(s.actions[:3]):
            button = QPushButton(action.label)
            button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            if i == 0:
                button.setObjectName("primary")
            button.clicked.connect(lambda _=False, idx=i: self._run_action(idx))
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addSpacing(4)
        layout.addLayout(buttons)

        self._status = QLabel(objectName="status", wordWrap=True)
        self._status.hide()
        layout.addWidget(self._status)
        if self.accept_hint or self.dismiss_hint:
            hint = QLabel(f"{self.accept_hint} accept · {self.dismiss_hint} dismiss", objectName="hint")
            layout.addWidget(hint)
        self._refit()

    def _build_info(self, text: str) -> None:
        layout = self._reset_content()
        label = QLabel(text, objectName="title", wordWrap=True)
        layout.addWidget(label)
        self._status = QLabel()
        self._refit()

    def _refit(self) -> None:
        self._content.adjustSize()
        self.adjustSize()
        self._place()

    # --- showing / hiding --------------------------------------------------

    def _place(self) -> None:
        screen = QApplication.primaryScreen().availableGeometry()
        corner = self.settings.corner
        x = screen.right() - self.width() - MARGIN if corner in (WidgetCorner.BOTTOM_RIGHT, WidgetCorner.TOP_RIGHT) \
            else screen.left() + MARGIN
        y = screen.bottom() - self.height() - MARGIN if corner in (WidgetCorner.BOTTOM_RIGHT, WidgetCorner.BOTTOM_LEFT) \
            else screen.top() + MARGIN
        self.move(x, y)

    def _present(self, ms: int) -> None:
        self._fading_out = False
        if not self.isVisible():
            self.setWindowOpacity(0.0)
            self.show()
        self._ensure_native()
        self.raise_()
        self._fade.stop()
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(1.0)
        self._fade.start()
        self._hide_timer.start(ms)

    def _ensure_native(self) -> None:
        if self._native_ready or sys.platform != "win32":
            return
        from ..winapi import glass

        hwnd = int(self.winId())
        glass.round_corners(hwnd)
        glass.exclude_from_capture(hwnd)
        self._acrylic = glass.enable_acrylic(hwnd, (20, 22, 28, 70))
        self._native_ready = True
        self.update()

    def _timed_out(self) -> None:
        if self.underMouse() and not self._busy and self._status.isHidden():
            self._hide_timer.start(1500)
            return
        if self.suggestion is not None and self._status.isHidden():
            self._finish("ignored")
        else:
            self._fade_out()

    def _finish(self, outcome: str) -> None:
        suggestion, self.suggestion = self.suggestion, None
        self._fade_out()
        if suggestion is not None:
            self.closed.emit(suggestion, outcome)

    def _run_action(self, index: int) -> None:
        self._busy = True
        self._hide_timer.stop()
        suggestion, self.suggestion = self.suggestion, None
        self.visibility_changed.emit(False)
        self.action_requested.emit(suggestion, index)

    def _fade_out(self) -> None:
        if not self.isVisible():
            return
        self._hide_timer.stop()
        self._fading_out = True
        self._fade.stop()
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.start()
        self.visibility_changed.emit(False)

    def _fade_finished(self) -> None:
        if self._fading_out:
            self.hide()
            self._fading_out = False

    # --- painting ------------------------------------------------------------

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, RADIUS, RADIUS)
        p.fillPath(path, QColor(22, 24, 30, 110 if self._acrylic else 236))
        sheen = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        sheen.setColorAt(0.0, QColor(255, 255, 255, 34))
        sheen.setColorAt(0.45, QColor(255, 255, 255, 6))
        sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.fillPath(path, sheen)
        p.setPen(QPen(QColor(255, 255, 255, 46), 1))
        p.drawPath(path)

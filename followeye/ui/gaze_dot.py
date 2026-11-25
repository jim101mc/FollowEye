from __future__ import annotations

import sys

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

SIZE = 30
HIDE_AFTER_MS = 1500


class GazeDot(QWidget):
    """Click-through dot showing where FollowEye thinks the user is looking."""

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(SIZE, SIZE)
        self.lost = False
        self._native_ready = False
        self._stale = QTimer(self, singleShot=True, interval=HIDE_AFTER_MS, timeout=self.hide)

    def update_gaze(self, x: float | None, y: float | None) -> None:
        """x, y in physical pixels of the primary screen; None while the eyes are not tracked."""
        self._stale.start()
        if x is None or y is None:
            if not self.lost:
                self.lost = True
                self.update()
            return
        dpr = QApplication.primaryScreen().devicePixelRatio()
        self.move(round(x / dpr - SIZE / 2), round(y / dpr - SIZE / 2))
        if self.lost:
            self.lost = False
            self.update()
        if not self.isVisible():
            self.show()
            self._ensure_native()

    def _ensure_native(self) -> None:
        if self._native_ready or sys.platform != "win32":
            return
        from ..winapi import glass

        glass.exclude_from_capture(int(self.winId()))  # keep it out of FollowEye's own screenshots
        self._native_ready = True

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(SIZE / 2, SIZE / 2)
        color = QColor(138, 143, 152) if self.lost else QColor(62, 199, 194)
        ring = QColor(color)
        ring.setAlpha(200)
        p.setPen(QPen(ring, 3))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(center, SIZE / 2 - 3, SIZE / 2 - 3)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawEllipse(center, 3.5, 3.5)

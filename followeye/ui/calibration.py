from __future__ import annotations

import time

import numpy as np
from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from ..config import Calibration
from ..gaze.model import GazeModel

POINTS = [(x, y) for y in (0.08, 0.5, 0.92) for x in (0.06, 0.5, 0.94)]
SETTLE_S = 0.7   # eyes travel to the dot
COLLECT_S = 0.9  # samples are recorded
MIN_SAMPLES_PER_POINT = 8
MIN_POINTS = 7


class CalibrationWindow(QWidget):
    """Full-screen sequence of dots on the main display; fits the gaze model from where the eyes were."""

    finished = Signal(object)  # Calibration | None
    feature = Signal(float, object)  # thread-safe entry point for the tracker's features

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setCursor(Qt.CursorShape.BlankCursor)
        self.screen_ = QApplication.primaryScreen()
        self.setGeometry(self.screen_.geometry())
        self.feature.connect(self._on_feature)
        self._timer = QTimer(self, interval=16, timeout=self._tick)
        self._message = ""
        self._result: GazeModel | None = None
        self._restart()

    def start(self) -> None:
        self.showFullScreen()
        self.activateWindow()
        self._timer.start()

    def _restart(self) -> None:
        self._index = -1
        self._samples: list[list[np.ndarray]] = [[] for _ in POINTS]
        self._result = None
        self._message = "Follow the dot with your eyes. Keep your head still. Esc cancels."
        self._intro_until = time.monotonic() + 2.5
        self._point_start = 0.0

    def _tick(self) -> None:
        now = time.monotonic()
        if self._index == -1 and now >= self._intro_until:
            self._index, self._point_start, self._message = 0, now, ""
        elif 0 <= self._index < len(POINTS) and now - self._point_start >= SETTLE_S + COLLECT_S:
            self._index += 1
            self._point_start = now
            if self._index == len(POINTS):
                self._timer.stop()
                self._fit()
        self.update()

    def _on_feature(self, t: float, features) -> None:
        if features is None or not 0 <= self._index < len(POINTS):
            return
        if t - self._point_start >= SETTLE_S:
            self._samples[self._index].append(features)

    def _physical(self, fx: float, fy: float) -> tuple[float, float]:
        geo = self.screen_.geometry()
        dpr = self.screen_.devicePixelRatio()
        return (geo.x() + fx * geo.width()) * dpr, (geo.y() + fy * geo.height()) * dpr

    def _fit(self) -> None:
        feats, targets = [], []
        good_points = 0
        for (fx, fy), samples in zip(POINTS, self._samples):
            if len(samples) < MIN_SAMPLES_PER_POINT:
                continue
            good_points += 1
            feats.extend(samples)
            targets.extend([self._physical(fx, fy)] * len(samples))
        if good_points < MIN_POINTS:
            self._message = ("Couldn't see your eyes well enough. Check the lighting and that the camera faces you.\n"
                             "R to retry · Esc to cancel")
            return
        geo = self.screen_.geometry()
        dpr = self.screen_.devicePixelRatio()
        screen = (int(geo.x() * dpr), int(geo.y() * dpr), int(geo.width() * dpr), int(geo.height() * dpr))
        self._result = GazeModel.fit(np.array(feats), np.array(targets, dtype=np.float64), screen)
        pct = 100 * self._result.mean_error_px / screen[2]
        self._message = (f"Done. Average error {self._result.mean_error_px:.0f} px (about {pct:.0f}% of the screen width).\n"
                         "Enter to save · R to redo · Esc to cancel")

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self._close(None)
        elif key == Qt.Key.Key_R and self._index >= len(POINTS):
            self._restart()
            self._timer.start()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._result is not None:
            self._close(self._result.to_calibration())

    def _close(self, result: Calibration | None) -> None:
        self._timer.stop()
        self.close()
        self.finished.emit(result)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(16, 18, 22))
        if 0 <= self._index < len(POINTS):
            fx, fy = POINTS[self._index]
            center = QPointF(fx * self.width(), fy * self.height())
            progress = min(1.0, (time.monotonic() - self._point_start) / (SETTLE_S + COLLECT_S))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(62, 199, 194, 90))
            p.drawEllipse(center, 26 * (1 - progress) + 8, 26 * (1 - progress) + 8)
            p.setBrush(QColor(255, 255, 255))
            p.drawEllipse(center, 5, 5)
        if self._message:
            p.setPen(QColor(230, 230, 235))
            p.setFont(QFont("Segoe UI", 16))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._message)

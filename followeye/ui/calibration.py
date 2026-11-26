from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from ..config import Calibration
from ..gaze.model import GazeModel, robust_mean_filter

POINTS = [(x, y) for y in (0.08, 0.5, 0.92) for x in (0.06, 0.5, 0.94)]
ROUNDS = (
    ("normal", "Round 1 of 3: sit as you normally do."),
    ("closer", "Round 2 of 3: move closer to the screen, about a hand's width."),
    ("farther", "Round 3 of 3: move back, a bit farther than usual."),
)
# The screen lights the face: changing its brightness teaches the model different lighting.
BACKGROUNDS = (QColor(240, 240, 240), QColor(128, 128, 128), QColor(14, 15, 18))
INTRO_BACKGROUND = QColor(16, 18, 22)
INTRO_S = 3.5    # time to read the instruction and change position
SETTLE_S = 0.7   # eyes travel to the dot
COLLECT_S = 0.9  # samples are recorded
MIN_SAMPLES_PER_POINT = 8
MIN_POINTS = 20  # of 27
MIN_POINTS_PER_ROUND = 5


@dataclass(frozen=True)
class _Step:
    round: int
    point: int | None  # None for the instruction screen before a round
    background: QColor

    @property
    def duration(self) -> float:
        return INTRO_S if self.point is None else SETTLE_S + COLLECT_S


def _steps() -> list[_Step]:
    steps = []
    for r in range(len(ROUNDS)):
        steps.append(_Step(r, None, INTRO_BACKGROUND))
        # Shift the background per round so every distance sees every brightness at different points.
        steps += [_Step(r, i, BACKGROUNDS[(i + r) % len(BACKGROUNDS)]) for i in range(len(POINTS))]
    return steps


def _contrast(color: QColor) -> QColor:
    return QColor(20, 20, 24) if color.lightness() > 140 else QColor(235, 235, 240)


class CalibrationWindow(QWidget):
    """Full-screen dots on the main display in three rounds (normal, closer, farther distance),
    with a white, gray or black background; fits the gaze model from where the eyes were."""

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
        self._steps = _steps()
        self._restart()

    def start(self) -> None:
        self.showFullScreen()
        self.activateWindow()
        self._timer.start()

    def _restart(self) -> None:
        self._index = -1
        self._samples: dict[tuple[int, int], list[np.ndarray]] = {}
        self._result: GazeModel | None = None
        self._message = ("Follow the dot with your eyes and keep your head still during each round.\n"
                         "The background changes brightness on purpose. Esc cancels.")
        self._step_start = time.monotonic()

    @property
    def _step(self) -> _Step | None:
        return self._steps[self._index] if 0 <= self._index < len(self._steps) else None

    def _tick(self) -> None:
        now = time.monotonic()
        duration = INTRO_S if self._index == -1 else self._step.duration
        if now - self._step_start >= duration:
            self._index += 1
            self._step_start = now
            if self._index == len(self._steps):
                self._timer.stop()
                self._fit()
        self.update()

    def _on_feature(self, t: float, features) -> None:
        step = self._step
        if features is None or step is None or step.point is None:
            return
        if t - self._step_start >= SETTLE_S:
            self._samples.setdefault((step.round, step.point), []).append(features)

    def _physical(self, fx: float, fy: float) -> tuple[float, float]:
        geo = self.screen_.geometry()
        dpr = self.screen_.devicePixelRatio()
        return (geo.x() + fx * geo.width()) * dpr, (geo.y() + fy * geo.height()) * dpr

    def _fit(self) -> None:
        feats, targets = [], []
        good_per_round = [0] * len(ROUNDS)
        for (r, i), samples in self._samples.items():
            kept = robust_mean_filter(np.array(samples))
            if len(kept) < MIN_SAMPLES_PER_POINT:
                continue
            good_per_round[r] += 1
            feats.extend(kept)
            targets.extend([self._physical(*POINTS[i])] * len(kept))
        weak = [ROUNDS[r][0] for r, n in enumerate(good_per_round) if n < MIN_POINTS_PER_ROUND]
        if sum(good_per_round) < MIN_POINTS:
            self._message = ("Couldn't see your eyes well enough. Check that your face is lit and the camera faces you.\n"
                             + self._weak_hint(weak) + "R to retry · Esc to cancel")
            return
        geo = self.screen_.geometry()
        dpr = self.screen_.devicePixelRatio()
        screen = (int(geo.x() * dpr), int(geo.y() * dpr), int(geo.width() * dpr), int(geo.height() * dpr))
        self._result = GazeModel.fit(np.array(feats), np.array(targets, dtype=np.float64), screen)
        pct = 100 * self._result.mean_error_px / screen[2]
        self._message = (f"Done. Average error {self._result.mean_error_px:.0f} px (about {pct:.0f}% of the screen width).\n"
                         + self._weak_hint(weak) + "Enter to save · R to redo · Esc to cancel")

    @staticmethod
    def _weak_hint(weak: list[str]) -> str:
        if not weak:
            return ""
        hints = {"closer": "not quite so close", "farther": "not quite so far back", "normal": "facing the camera"}
        return "Your eyes were often lost in the " + ", ".join(f"“{w}” round (try {hints[w]})" for w in weak) + ".\n"

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self._close(None)
        elif key == Qt.Key.Key_R and self._index >= len(self._steps):
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
        step = self._step
        background = step.background if step else INTRO_BACKGROUND
        p.fillRect(self.rect(), background)
        fg = _contrast(background)
        p.setFont(QFont("Segoe UI", 18))
        if step is None:
            p.setPen(fg)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._message)
        elif step.point is None:
            remaining = max(0.0, INTRO_S - (time.monotonic() - self._step_start))
            p.setPen(fg)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       f"{ROUNDS[step.round][1]}\nStarting in {remaining:.0f} s")
        else:
            fx, fy = POINTS[step.point]
            center = QPointF(fx * self.width(), fy * self.height())
            progress = min(1.0, (time.monotonic() - self._step_start) / step.duration)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(62, 199, 194, 150))
            p.drawEllipse(center, 26 * (1 - progress) + 8, 26 * (1 - progress) + 8)
            p.setBrush(fg)
            p.drawEllipse(center, 5, 5)

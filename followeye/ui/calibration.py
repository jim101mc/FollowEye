from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from ..config import Calibration
from ..gaze.model import AccuracyReport, GazeModel, accuracy_report, robust_mean_filter

POINTS = [(x, y) for y in (0.08, 0.5, 0.92) for x in (0.06, 0.5, 0.94)]
ROUNDS = (
    ("normal", "Round 1 of 3: sit as you normally do."),
    ("closer", "Round 2 of 3: move closer to the screen, about a hand's width."),
    ("farther", "Round 3 of 3: move back, a bit farther than usual."),
)
# The screen lights the face: changing its brightness teaches the model different lighting.
BACKGROUNDS = (QColor(240, 240, 240), QColor(128, 128, 128), QColor(14, 15, 18))
INTRO_BACKGROUND = QColor(16, 18, 22)
# Accuracy test: new dots, never used for training, so the error shown is honest.
TEST_POINTS = [(0.25, 0.28), (0.75, 0.28), (0.5, 0.18), (0.3, 0.74), (0.7, 0.74)]
TEST_ROUND = len(ROUNDS)
TEST_TEXT = "Last step: an accuracy test with 5 new dots. Sit as you normally do."
TEST_BACKGROUND = QColor(128, 128, 128)
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
    steps.append(_Step(TEST_ROUND, None, INTRO_BACKGROUND))
    steps += [_Step(TEST_ROUND, i, TEST_BACKGROUND) for i in range(len(TEST_POINTS))]
    return steps


def _point(step_round: int, index: int) -> tuple[float, float]:
    return TEST_POINTS[index] if step_round == TEST_ROUND else POINTS[index]


def describe_accuracy(report: AccuracyReport, screen_width: int) -> str:
    """Plain-language summary: overall error, constant offset and jitter."""
    pct = 100 * report.error_px / screen_width
    parts = [f"On new dots it is off by {report.error_px:.0f} px on average (about {pct:.0f}% of the screen width)."]
    dx, dy = report.bias_px
    shifts = []
    if abs(dx) >= 0.4 * report.error_px and abs(dx) >= 20:
        shifts.append(f"{abs(dx):.0f} px too far {'right' if dx > 0 else 'left'}")
    if abs(dy) >= 0.4 * report.error_px and abs(dy) >= 20:
        shifts.append(f"{abs(dy):.0f} px too {'low' if dy > 0 else 'high'}")
    if shifts:
        parts.append("It tends to land " + " and ".join(shifts) + "; clicking around will correct this.")
    parts.append(f"Jitter: {report.jitter_px:.0f} px.")
    return " ".join(parts)


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
        self._test: AccuracyReport | None = None
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
        feats, targets, test_points = [], [], []
        good_per_round = [0] * len(ROUNDS)
        for (r, i), samples in self._samples.items():
            kept = robust_mean_filter(np.array(samples))
            if len(kept) < MIN_SAMPLES_PER_POINT:
                continue
            if r == TEST_ROUND:
                test_points.append((kept, self._physical(*TEST_POINTS[i])))
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
        self._test = accuracy_report(self._result, test_points) if len(test_points) >= 3 else None
        if self._test is not None:
            summary = describe_accuracy(self._test, screen[2])
        else:
            summary = "The accuracy test didn't get enough data, so its result is missing."
        self._message = "Done. " + summary + "\n" + self._weak_hint(weak) + "Enter to save · R to redo · Esc to cancel"

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
            self._close(self._result.to_calibration(self._test))

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
            p.drawText(self.rect().adjusted(160, 0, -160, 0), int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap), self._message)
        elif step.point is None:
            remaining = max(0.0, INTRO_S - (time.monotonic() - self._step_start))
            p.setPen(fg)
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                       f"{TEST_TEXT if step.round == TEST_ROUND else ROUNDS[step.round][1]}\nStarting in {remaining:.0f} s")
        else:
            fx, fy = _point(step.round, step.point)
            center = QPointF(fx * self.width(), fy * self.height())
            progress = min(1.0, (time.monotonic() - self._step_start) / step.duration)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(62, 199, 194, 150))
            p.drawEllipse(center, 26 * (1 - progress) + 8, 26 * (1 - progress) + 8)
            p.setBrush(fg)
            p.drawEllipse(center, 5, 5)

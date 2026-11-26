from __future__ import annotations

import math
import threading
from collections import deque
from dataclasses import dataclass

import numpy as np

MIN_CLICK_GAP_S = 0.3   # ignore the second click of a double click
MIN_FOR_OFFSET = 3      # clicks before a constant shift is applied
MIN_FOR_AFFINE = 15     # clicks before scale/rotation is corrected too
RIDGE = 0.05            # keeps the scale/rotation part close to "no change" until clicks show otherwise
PRIOR_CLICKS = 3.0      # the shift starts as if 3 clicks had shown no drift, so a few noisy clicks can't push it far


@dataclass(frozen=True)
class ClickSample:
    t: float
    raw: tuple[float, float]     # what the calibrated model predicted, before click correction
    target: tuple[float, float]  # where the click was: where the user was almost certainly looking


class ClickRecalibrator:
    """Corrects drift in the gaze estimate using mouse clicks as free calibration points.

    The correction is fitted on recent clicks, newer ones weighing more: first a constant
    shift, then, with enough clicks, an affine correction regularised towards no change.
    Thread-safe: clicks arrive on the mouse-hook thread, corrections are read by the tracker.
    """

    def __init__(self, screen: tuple[int, int, int, int], half_life_s: float = 900.0, max_samples: int = 200):
        self.left, self.top, self.width, self.height = screen
        self.half_life_s = half_life_s
        self._samples: deque[ClickSample] = deque(maxlen=max_samples)
        self._errors: deque[float] = deque(maxlen=50)  # out-of-sample error of each accepted click
        self._coef = np.zeros((3, 2))  # residual model on normalised [x, y, 1]
        self._last_click = -math.inf
        self.rejected = 0
        self._lock = threading.Lock()

    def _norm(self, x: float, y: float) -> np.ndarray:
        cx, cy = self.left + self.width / 2, self.top + self.height / 2
        return np.array([(x - cx) / self.width, (y - cy) / self.height, 1.0])

    def correct(self, x: float, y: float) -> tuple[float, float]:
        with self._lock:
            dx, dy = self._norm(x, y) @ self._coef
        return x + float(dx), y + float(dy)

    def gate_px(self) -> float:
        """Clicks farther than this from the corrected estimate are taken as 'not looking there'."""
        with self._lock:
            recent = list(self._errors)[-15:]
        if len(recent) < 5:
            return 0.35 * self.width
        return max(0.15 * self.width, 3.0 * float(np.median(recent)))

    def add(self, t: float, raw: tuple[float, float], target: tuple[float, float]) -> bool:
        """Record a click. Returns False if it was ignored (double click or not looked at)."""
        if t - self._last_click < MIN_CLICK_GAP_S:
            return False
        self._last_click = t
        cx, cy = self.correct(*raw)
        error = math.dist((cx, cy), target)
        if error > self.gate_px():
            self.rejected += 1
            return False
        with self._lock:
            self._errors.append(error)
            self._samples.append(ClickSample(t, raw, target))
            self._refit(t)
        return True

    def _refit(self, now: float) -> None:
        n = len(self._samples)
        if n < MIN_FOR_OFFSET:
            self._coef = np.zeros((3, 2))
            return
        w = np.array([0.5 ** ((now - s.t) / self.half_life_s) for s in self._samples])
        x = np.array([self._norm(*s.raw) for s in self._samples])
        r = np.array([(s.target[0] - s.raw[0], s.target[1] - s.raw[1]) for s in self._samples])
        if n < MIN_FOR_AFFINE:
            coef = np.zeros((3, 2))
            coef[2] = (w[:, None] * r).sum(axis=0) / (w.sum() + PRIOR_CLICKS)
        else:
            xw = x * w[:, None]
            reg = np.diag([RIDGE, RIDGE, PRIOR_CLICKS])
            coef = np.linalg.solve(x.T @ xw + reg, xw.T @ r)
        self._coef = coef

    def reset(self) -> None:
        with self._lock:
            self._samples.clear()
            self._errors.clear()
            self._coef = np.zeros((3, 2))
            self.rejected = 0

    @property
    def clicks_used(self) -> int:
        return len(self._samples)

    @property
    def live_error_px(self) -> float | None:
        """Average distance between estimate and click over recent clicks, measured before learning from them."""
        with self._lock:
            return float(np.mean(self._errors)) if len(self._errors) >= 5 else None

    @property
    def shift_px(self) -> tuple[float, float]:
        """Current correction at the screen centre: how far the raw estimate is being moved."""
        with self._lock:
            return float(self._coef[2, 0]), float(self._coef[2, 1])

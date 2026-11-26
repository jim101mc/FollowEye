from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..config import Calibration

# MediaPipe Face Landmarker indices (478 points including irises).
# "A" is the eye on the left of the camera image, "B" the one on the right.
EYE_A = {"left": 33, "right": 133, "top": 159, "bottom": 145, "iris": 468}
EYE_B = {"left": 362, "right": 263, "top": 386, "bottom": 374, "iris": 473}
BLINK_RATIO = 0.12
# Bump when the features change: older calibrations can't be used and must be redone.
CALIBRATION_VERSION = 2


def head_pose(matrix: np.ndarray) -> np.ndarray:
    """[yaw, pitch, roll (radians), x, y, z (cm)] from MediaPipe's 4x4 facial transformation matrix."""
    r = matrix[:3, :3]
    yaw = math.atan2(r[0, 2], r[2, 2])
    pitch = math.asin(max(-1.0, min(1.0, -r[1, 2])))
    roll = math.atan2(r[1, 0], r[1, 1])
    return np.array([yaw, pitch, roll, *matrix[:3, 3]], dtype=np.float64)


def eye_ratios(lm: np.ndarray) -> tuple[float, float] | None:
    """Iris position inside the eye openings (0-1, averaged over both eyes); None while blinking."""
    if lm.shape[0] < 478:
        return None
    ratios = []
    for eye in (EYE_A, EYE_B):
        left, right = lm[eye["left"], :2], lm[eye["right"], :2]
        top, bottom, iris = lm[eye["top"], :2], lm[eye["bottom"], :2], lm[eye["iris"], :2]
        width = right[0] - left[0]
        height = bottom[1] - top[1]
        if width <= 1e-6 or height / width < BLINK_RATIO:
            return None
        ratios.append(((iris[0] - left[0]) / width, (iris[1] - top[1]) / height))
    return (ratios[0][0] + ratios[1][0]) / 2, (ratios[0][1] + ratios[1][1]) / 2


def extract_features(lm: np.ndarray, matrix: np.ndarray) -> np.ndarray | None:
    """[eye_x, eye_y, yaw, pitch, roll, x, y, z] from landmarks and head pose; None while blinking."""
    eyes = eye_ratios(lm)
    if eyes is None:
        return None
    return np.concatenate([eyes, head_pose(matrix)])


def _design(z: np.ndarray) -> np.ndarray:
    """Model terms from normalized features: quadratic in the eyes, linear in head pose,
    plus eye x distance terms (the same eye movement covers more screen when farther away)."""
    z = np.atleast_2d(z)
    hx, vy, dist = z[:, 0], z[:, 1], z[:, 7]
    cols = [np.ones(len(z)), hx, vy, hx * hx, hx * vy, vy * vy, *z[:, 2:8].T, hx * dist, vy * dist]
    return np.stack(cols, axis=1)


@dataclass(frozen=True)
class AccuracyReport:
    error_px: float                # mean distance from target, using each point's averaged estimate
    bias_px: tuple[float, float]   # mean (right, down) offset: a constant shift in one direction
    jitter_px: float               # mean spread of single-frame estimates around their average


def accuracy_report(model: GazeModel, points: list[tuple[np.ndarray, tuple[float, float]]]) -> AccuracyReport | None:
    """Accuracy on dots the model was not trained on. `points` holds (samples, target) per dot."""
    errors, offsets, spreads = [], [], []
    for samples, target in points:
        if len(samples) == 0:
            continue
        preds = np.array([model.predict(s) for s in samples])
        mean = preds.mean(axis=0)
        offsets.append(mean - np.asarray(target))
        errors.append(float(np.linalg.norm(mean - np.asarray(target))))
        spreads.append(float(np.sqrt(np.mean(np.sum((preds - mean) ** 2, axis=1)))))
    if not errors:
        return None
    bias = np.mean(offsets, axis=0)
    return AccuracyReport(float(np.mean(errors)), (float(bias[0]), float(bias[1])), float(np.mean(spreads)))


def robust_mean_filter(samples: np.ndarray, k: float = 2.5) -> np.ndarray:
    """Drop samples far from the median (eyes still travelling, blinks half-detected)."""
    if len(samples) < 4:
        return samples
    median = np.median(samples, axis=0)
    mad = np.median(np.abs(samples - median), axis=0)
    mad[mad < 1e-9] = 1e-9
    keep = np.all(np.abs(samples - median) <= k * 1.4826 * mad, axis=1)
    return samples[keep] if keep.sum() >= len(samples) // 2 else samples


class GazeModel:
    """Maps gaze features to physical screen pixels with ridge-regularised polynomial regression."""

    def __init__(self, coefficients: np.ndarray, mean: np.ndarray, std: np.ndarray, screen, error: float = 0.0):
        self.coefficients = coefficients
        self.mean = mean
        self.std = std
        self.screen = tuple(screen)
        self.mean_error_px = error

    @classmethod
    def fit(cls, features: np.ndarray, targets: np.ndarray, screen, ridge: float = 1e-2) -> GazeModel:
        mean = features.mean(axis=0)
        std = features.std(axis=0)
        std[std < 1e-9] = 1.0
        x = _design((features - mean) / std)
        reg = ridge * np.eye(x.shape[1])
        reg[0, 0] = 0.0
        coefficients = np.linalg.solve(x.T @ x + reg, x.T @ targets)
        error = float(np.mean(np.linalg.norm(x @ coefficients - targets, axis=1)))
        return cls(coefficients, mean, std, screen, error)

    def predict(self, features: np.ndarray) -> tuple[float, float]:
        x = _design((features - self.mean) / self.std)
        px, py = (x @ self.coefficients)[0]
        return float(px), float(py)

    def on_screen(self, x: float, y: float, margin: float = 0.1) -> bool:
        left, top, w, h = self.screen
        return (left - margin * w <= x <= left + w * (1 + margin)) and (top - margin * h <= y <= top + h * (1 + margin))

    def to_calibration(self, test: AccuracyReport | None = None) -> Calibration:
        return Calibration(
            coefficients=self.coefficients.tolist(),
            feature_mean=self.mean.tolist(),
            feature_std=self.std.tolist(),
            screen=self.screen,
            mean_error_px=self.mean_error_px,
            test_error_px=test.error_px if test else None,
            test_bias_px=test.bias_px if test else None,
            test_jitter_px=test.jitter_px if test else None,
            version=CALIBRATION_VERSION,
        )

    @classmethod
    def from_calibration(cls, c: Calibration) -> GazeModel:
        return cls(np.array(c.coefficients), np.array(c.feature_mean), np.array(c.feature_std), c.screen, c.mean_error_px)


class OneEuroFilter:
    """Adaptive low-pass filter: smooth when the gaze is still, responsive when it moves."""

    def __init__(self, min_cutoff: float = 1.0, beta: float = 0.01, d_cutoff: float = 1.0):
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        self._x: np.ndarray | None = None
        self._dx = np.zeros(2)
        self._t = 0.0

    @staticmethod
    def _alpha(cutoff: float, dt: float) -> float:
        tau = 1.0 / (2 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def reset(self) -> None:
        self._x = None

    def __call__(self, t: float, x: tuple[float, float]) -> tuple[float, float]:
        value = np.asarray(x, dtype=np.float64)
        if self._x is None or t <= self._t:
            self._x, self._t = value, t
            return x
        dt = t - self._t
        a_d = self._alpha(self.d_cutoff, dt)
        self._dx = a_d * (value - self._x) / dt + (1 - a_d) * self._dx
        cutoff = self.min_cutoff + self.beta * float(np.linalg.norm(self._dx))
        a = self._alpha(cutoff, dt)
        self._x = a * value + (1 - a) * self._x
        self._t = t
        return float(self._x[0]), float(self._x[1])


def filter_for_smoothing(smoothing: float) -> OneEuroFilter:
    """smoothing in [0, 1]: 0 is nearly raw, 1 is heavily smoothed."""
    smoothing = min(max(smoothing, 0.0), 1.0)
    # beta multiplies the speed in pixels per second, so it must be small: larger values let jitter through.
    return OneEuroFilter(min_cutoff=1.5 - 1.3 * smoothing, beta=0.003 - 0.0025 * smoothing)

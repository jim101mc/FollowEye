from __future__ import annotations

import math

import numpy as np

from ..config import Calibration

# MediaPipe Face Mesh landmark indices (with refine_landmarks=True, 478 points).
# "A" is the eye on the left of the camera image, "B" the one on the right.
EYE_A = {"left": 33, "right": 133, "top": 159, "bottom": 145, "iris": 468}
EYE_B = {"left": 362, "right": 263, "top": 386, "bottom": 374, "iris": 473}
NOSE_TIP, FACE_LEFT, FACE_RIGHT, FOREHEAD, CHIN = 1, 234, 454, 10, 152
BLINK_RATIO = 0.12
N_FEATURES = 6


def extract_features(lm: np.ndarray) -> np.ndarray | None:
    """Gaze features from a (478, >=2) array of normalized landmarks; None while blinking."""
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
    hx = (ratios[0][0] + ratios[1][0]) / 2
    vy = (ratios[0][1] + ratios[1][1]) / 2
    face_w = lm[FACE_RIGHT, 0] - lm[FACE_LEFT, 0]
    face_h = lm[CHIN, 1] - lm[FOREHEAD, 1]
    if face_w <= 1e-6 or face_h <= 1e-6:
        return None
    nose = lm[NOSE_TIP, :2]
    yaw = (nose[0] - (lm[FACE_LEFT, 0] + lm[FACE_RIGHT, 0]) / 2) / face_w
    pitch = (nose[1] - (lm[FOREHEAD, 1] + lm[CHIN, 1]) / 2) / face_h
    return np.array([hx, vy, nose[0], nose[1], yaw, pitch], dtype=np.float64)


def _poly2(z: np.ndarray) -> np.ndarray:
    """Second-order polynomial expansion with bias: [1, z_i, z_i*z_j (i<=j)]."""
    z = np.atleast_2d(z)
    n, d = z.shape
    cols = [np.ones(n)] + [z[:, i] for i in range(d)]
    cols += [z[:, i] * z[:, j] for i in range(d) for j in range(i, d)]
    return np.stack(cols, axis=1)


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
        x = _poly2((features - mean) / std)
        reg = ridge * np.eye(x.shape[1])
        reg[0, 0] = 0.0
        coefficients = np.linalg.solve(x.T @ x + reg, x.T @ targets)
        error = float(np.mean(np.linalg.norm(x @ coefficients - targets, axis=1)))
        return cls(coefficients, mean, std, screen, error)

    def predict(self, features: np.ndarray) -> tuple[float, float]:
        x = _poly2((features - self.mean) / self.std)
        px, py = (x @ self.coefficients)[0]
        return float(px), float(py)

    def on_screen(self, x: float, y: float, margin: float = 0.1) -> bool:
        left, top, w, h = self.screen
        return (left - margin * w <= x <= left + w * (1 + margin)) and (top - margin * h <= y <= top + h * (1 + margin))

    def to_calibration(self) -> Calibration:
        return Calibration(
            coefficients=self.coefficients.tolist(),
            feature_mean=self.mean.tolist(),
            feature_std=self.std.tolist(),
            screen=self.screen,
            mean_error_px=self.mean_error_px,
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
    return OneEuroFilter(min_cutoff=3.0 - 2.7 * smoothing, beta=0.02 - 0.015 * smoothing)

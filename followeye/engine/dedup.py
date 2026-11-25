from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

import numpy as np

from ..modes import Trigger

THUMB_W, THUMB_H = 64, 36


def thumbnail(bgra: np.ndarray) -> np.ndarray:
    """Downsample a BGRA/BGR screenshot to a small grayscale image by area averaging."""
    h, w = bgra.shape[:2]
    sy, sx = max(h // THUMB_H, 1), max(w // THUMB_W, 1)
    rows, cols = min(THUMB_H, h // sy), min(THUMB_W, w // sx)
    gray = bgra[: rows * sy, : cols * sx, :3].astype(np.float32).mean(axis=2)
    return gray.reshape(rows, sy, cols, sx).mean(axis=(1, 3))


@dataclass
class _WindowState:
    thumb: np.ndarray
    content_hash: str
    analyzed_at: float


class Deduplicator:
    """Skips re-analysing a window whose pixels and text haven't changed since the last analysis."""

    def __init__(
        self,
        pixel_change: float = 0.05,
        cooldown: float = 30.0,
        diff_level: float = 16.0,
        max_windows: int = 64,
    ):
        self.pixel_change = pixel_change
        self.cooldown = cooldown
        self.diff_level = diff_level
        self.max_windows = max_windows
        self._state: OrderedDict[int, _WindowState] = OrderedDict()

    def should_capture(self, key: int, thumb: np.ndarray, t: float, trigger: Trigger) -> bool:
        prev = self._state.get(key)
        if prev is None or trigger is Trigger.CLIPBOARD or t - prev.analyzed_at >= self.cooldown:
            return True
        if prev.thumb.shape != thumb.shape:
            return True
        changed = np.mean(np.abs(thumb - prev.thumb) > self.diff_level)
        return bool(changed >= self.pixel_change)

    def should_analyze(self, key: int, content_hash: str, t: float) -> bool:
        prev = self._state.get(key)
        return prev is None or prev.content_hash != content_hash or t - prev.analyzed_at >= self.cooldown

    def mark(self, key: int, thumb: np.ndarray, content_hash: str, t: float) -> None:
        self._state[key] = _WindowState(thumb, content_hash, t)
        self._state.move_to_end(key)
        while len(self._state) > self.max_windows:
            self._state.popitem(last=False)

    def clear(self) -> None:
        self._state.clear()

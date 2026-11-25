from __future__ import annotations

import threading

import cv2
import mss
import numpy as np

MIN_SIZE = 40
_local = threading.local()


def _sct() -> mss.base.MSSBase:
    # mss instances are not thread-safe; keep one per thread.
    if not hasattr(_local, "sct"):
        _local.sct = mss.mss()
    return _local.sct


def grab(rect: tuple[int, int, int, int]) -> np.ndarray | None:
    """BGRA pixels of a screen rectangle (left, top, right, bottom), clipped to the desktop."""
    sct = _sct()
    desktop = sct.monitors[0]
    left = max(rect[0], desktop["left"])
    top = max(rect[1], desktop["top"])
    right = min(rect[2], desktop["left"] + desktop["width"])
    bottom = min(rect[3], desktop["top"] + desktop["height"])
    if right - left < MIN_SIZE or bottom - top < MIN_SIZE:
        return None
    shot = sct.grab({"left": left, "top": top, "width": right - left, "height": bottom - top})
    return np.asarray(shot)


def to_png(bgra: np.ndarray, max_side: int = 1568) -> bytes:
    h, w = bgra.shape[:2]
    scale = min(1.0, max_side / max(h, w))
    image = cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)
    if scale < 1.0:
        image = cv2.resize(image, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    ok, data = cv2.imencode(".png", image)
    return data.tobytes() if ok else b""

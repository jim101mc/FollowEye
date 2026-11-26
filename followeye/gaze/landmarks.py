from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

MODEL_PATH = Path(__file__).resolve().parent.parent / "assets" / "face_landmarker.task"
DARK_MEAN = 90  # average brightness (0-255) below which frames are brightened


@dataclass(frozen=True)
class Face:
    points: np.ndarray  # (478, 2) normalized image coordinates
    matrix: np.ndarray  # (4, 4) head pose: canonical face -> camera space, translation in cm


_clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))


def brighten_if_dark(frame_bgr: np.ndarray) -> tuple[np.ndarray, bool]:
    """Evens out and lifts the brightness of dark frames so the eyes can still be found."""
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    if gray.mean() >= DARK_MEAN:
        return frame_bgr, False
    lab = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2LAB)
    lab[:, :, 0] = _clahe.apply(lab[:, :, 0])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR), True


class FaceLandmarks:
    """MediaPipe Face Landmarker (478 points including irises, plus head pose) in video mode."""

    def __init__(self, model_path: Path = MODEL_PATH):
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision

        self._mp = mp
        options = vision.FaceLandmarkerOptions(
            # A buffer avoids MediaPipe's trouble with non-ASCII install paths on Windows.
            base_options=BaseOptions(model_asset_buffer=model_path.read_bytes()),
            running_mode=vision.RunningMode.VIDEO,
            num_faces=1,
            min_face_detection_confidence=0.5,
            min_face_presence_confidence=0.5,
            min_tracking_confidence=0.5,
            output_facial_transformation_matrixes=True,
        )
        self._landmarker = vision.FaceLandmarker.create_from_options(options)
        self._last_ms = -1

    def detect(self, rgb: np.ndarray, t: float) -> Face | None:
        """The first face in the frame, or None. `t` is a monotonic time in seconds."""
        ms = max(int(t * 1000), self._last_ms + 1)  # video mode needs strictly increasing timestamps
        self._last_ms = ms
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
        result = self._landmarker.detect_for_video(image, ms)
        if not result.face_landmarks or not result.facial_transformation_matrixes:
            return None
        points = np.array([(p.x, p.y) for p in result.face_landmarks[0]])
        return Face(points, np.asarray(result.facial_transformation_matrixes[0], dtype=np.float64))

    def close(self) -> None:
        self._landmarker.close()

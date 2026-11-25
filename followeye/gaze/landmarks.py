from __future__ import annotations

from pathlib import Path

import numpy as np

MODEL_PATH = Path(__file__).resolve().parent.parent / "assets" / "face_landmarker.task"


class FaceLandmarks:
    """MediaPipe Face Landmarker (478 points including irises) in video mode."""

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
        )
        self._landmarker = vision.FaceLandmarker.create_from_options(options)
        self._last_ms = -1

    def detect(self, rgb: np.ndarray, t: float) -> np.ndarray | None:
        """Normalized (478, 2) landmarks of the first face, or None. `t` is a monotonic time in seconds."""
        ms = max(int(t * 1000), self._last_ms + 1)  # video mode needs strictly increasing timestamps
        self._last_ms = ms
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
        result = self._landmarker.detect_for_video(image, ms)
        if not result.face_landmarks:
            return None
        return np.array([(p.x, p.y) for p in result.face_landmarks[0]])

    def close(self) -> None:
        self._landmarker.close()

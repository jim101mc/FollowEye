from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from .model import GazeModel, OneEuroFilter, extract_features

log = logging.getLogger(__name__)

TARGET_FPS = 30


@dataclass(frozen=True)
class GazeSample:
    t: float
    x: float | None  # physical screen pixels, None when no face/blinking/looking away
    y: float | None


FeatureCallback = Callable[[float, np.ndarray | None], None]
GazeCallback = Callable[[GazeSample], None]
FrameCallback = Callable[[np.ndarray, np.ndarray | None], None]


class GazeTracker:
    """Reads the webcam at 30 fps, extracts eye features with MediaPipe and maps them to the screen."""

    def __init__(self, camera_index: int, on_gaze: GazeCallback, on_error: Callable[[str], None]):
        self.camera_index = camera_index
        self._on_gaze = on_gaze
        self._on_error = on_error
        self._model: GazeModel | None = None
        self._filter: OneEuroFilter | None = None
        self._feature_listener: FeatureCallback | None = None
        self._frame_listener: FrameCallback | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def set_model(self, model: GazeModel | None, smoothing_filter: OneEuroFilter | None) -> None:
        with self._lock:
            self._model = model
            self._filter = smoothing_filter

    def set_feature_listener(self, listener: FeatureCallback | None) -> None:
        with self._lock:
            self._feature_listener = listener

    def set_frame_listener(self, listener: FrameCallback | None) -> None:
        """Receives every camera frame (BGR) with its face landmarks, or None if no face was found."""
        with self._lock:
            self._frame_listener = listener

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="gaze-tracker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

    def _run(self) -> None:
        import cv2
        import mediapipe as mp

        cap = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)
        if not cap.isOpened():
            self._on_error(f"Could not open camera {self.camera_index}.")
            return
        cap.set(cv2.CAP_PROP_FPS, TARGET_FPS)
        frame_time = 1.0 / TARGET_FPS
        try:
            with mp.solutions.face_mesh.FaceMesh(
                max_num_faces=1,
                refine_landmarks=True,
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5,
            ) as mesh:
                while not self._stop.is_set():
                    started = time.monotonic()
                    ok, frame = cap.read()
                    if not ok:
                        self._on_error("The camera stopped delivering frames.")
                        return
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    result = mesh.process(rgb)
                    points = None
                    if result.multi_face_landmarks:
                        lm = result.multi_face_landmarks[0].landmark
                        points = np.array([(p.x, p.y) for p in lm])
                    with self._lock:
                        frame_listener = self._frame_listener
                    if frame_listener is not None:
                        frame_listener(frame, points)
                    self._publish(started, extract_features(points) if points is not None else None)
                    time.sleep(max(0.0, frame_time - (time.monotonic() - started)))
        finally:
            cap.release()

    def _publish(self, t: float, features: np.ndarray | None) -> None:
        with self._lock:
            listener, model, smoother = self._feature_listener, self._model, self._filter
        if listener is not None:
            listener(t, features)
        if model is None:
            return
        if features is None:
            self._on_gaze(GazeSample(t, None, None))
            return
        x, y = model.predict(features)
        if not model.on_screen(x, y):
            if smoother:
                smoother.reset()
            self._on_gaze(GazeSample(t, None, None))
            return
        if smoother:
            x, y = smoother(t, (x, y))
        self._on_gaze(GazeSample(t, x, y))

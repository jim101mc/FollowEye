from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..gaze.model import EYE_A, EYE_B, extract_features

PREVIEW_WIDTH = 480
GREEN = (120, 220, 120)
TEAL = (194, 199, 62)  # BGR

NO_FACE = "No face found. Face the camera and make sure your face is well lit."
EYES_CLOSED = "Face found, but the eyes are closed or hidden (glare on glasses?)."
TRACKING = "Tracking both eyes."


def annotate(frame_bgr: np.ndarray, points: np.ndarray | None) -> tuple[np.ndarray, str]:
    """Mirror the frame like a selfie view, draw eye outlines and irises; returns (RGB image, status)."""
    image = frame_bgr.copy()
    h, w = image.shape[:2]
    if points is None or points.shape[0] < 478:
        status = NO_FACE
    else:
        status = TRACKING if extract_features(points) is not None else EYES_CLOSED
        for eye in (EYE_A, EYE_B):
            for key in ("left", "right", "top", "bottom"):
                x, y = points[eye[key]]
                cv2.circle(image, (int(x * w), int(y * h)), 2, GREEN, -1, cv2.LINE_AA)
            ix, iy = points[eye["iris"]]
            cv2.circle(image, (int(ix * w), int(iy * h)), 5, TEAL, 2, cv2.LINE_AA)
    image = cv2.flip(image, 1)
    scale = PREVIEW_WIDTH / w
    if scale < 1:
        image = cv2.resize(image, (PREVIEW_WIDTH, int(h * scale)), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB), status


class CameraPreview(QWidget):
    """Live webcam view with the tracked eye points, to check lighting and position."""

    closed = Signal()
    frame = Signal(object, str)  # thread-safe entry point: RGB image, status

    def __init__(self):
        super().__init__(None)
        self.setWindowTitle("FollowEye camera")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        self.image = QLabel("Starting camera…", alignment=Qt.AlignmentFlag.AlignCenter)
        self.image.setMinimumSize(PREVIEW_WIDTH, 270)
        self.status = QLabel(wordWrap=True)
        hint = QLabel("Green dots: eye corners and lids. Ring: iris. The image is mirrored.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        layout = QVBoxLayout(self)
        layout.addWidget(self.image)
        layout.addWidget(self.status)
        layout.addWidget(hint)
        self.frame.connect(self._show_frame)

    def _show_frame(self, rgb: np.ndarray, status: str) -> None:
        h, w = rgb.shape[:2]
        image = QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
        self.image.setPixmap(QPixmap.fromImage(image))
        self.status.setText(status)

    def show_error(self, message: str) -> None:
        self.image.setPixmap(QPixmap())
        self.image.setText(message)
        self.status.setText("")

    def closeEvent(self, event) -> None:
        self.closed.emit()
        super().closeEvent(event)

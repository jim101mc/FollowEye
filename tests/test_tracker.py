import time

import numpy as np
import pytest

from followeye.gaze.tracker import GazeTracker


def _mediapipe_works():
    try:
        from followeye.gaze.landmarks import FaceLandmarks

        FaceLandmarks().close()
        return True
    except Exception:  # library or its native dependencies missing on this machine
        return False


needs_mediapipe = pytest.mark.skipif(not _mediapipe_works(), reason="MediaPipe not usable here")


@needs_mediapipe
def test_landmarker_runs_on_bundled_model():
    from followeye.gaze.landmarks import MODEL_PATH, FaceLandmarks

    assert MODEL_PATH.is_file()
    landmarks = FaceLandmarks()
    blank = np.zeros((480, 640, 3), np.uint8)
    assert landmarks.detect(blank, 1.0) is None
    assert landmarks.detect(blank, 1.0) is None  # same timestamp is bumped, not rejected
    landmarks.close()


@needs_mediapipe
def test_self_test_reports_ok(tmp_path):
    from followeye.__main__ import self_test

    report = tmp_path / "report.txt"
    assert self_test(str(report)) == 0
    assert report.read_text() == "ok"


def test_tracker_reports_crashes_instead_of_dying_silently(monkeypatch):
    errors = []
    tracker = GazeTracker(0, on_gaze=lambda s: None, on_error=errors.append)

    def boom():
        raise AttributeError("module 'mediapipe' has no attribute 'solutions'")

    monkeypatch.setattr(tracker, "_loop", boom)
    tracker.start()
    deadline = time.monotonic() + 2
    while not errors and time.monotonic() < deadline:
        time.sleep(0.01)
    assert errors and "Eye tracking stopped" in errors[0]


def test_tracker_reports_unopenable_camera(monkeypatch):
    errors = []
    tracker = GazeTracker(7, on_gaze=lambda s: None, on_error=errors.append)
    monkeypatch.setattr(tracker, "_open_camera", lambda: None)
    tracker._run()
    assert "Could not open camera 7" in errors[0] and "camera number" in errors[0]

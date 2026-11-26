import math

import numpy as np

from followeye.gaze.adapt import MIN_FOR_AFFINE, ClickRecalibrator
from followeye.gaze.model import GazeModel, OneEuroFilter
from followeye.gaze.tracker import GazeTracker

SCREEN = (0, 0, 1920, 1080)


def clicks(recal, n, raw_of, start_t=0.0, seed=0):
    rng = np.random.default_rng(seed)
    t = start_t
    for _ in range(n):
        target = (float(rng.uniform(100, 1820)), float(rng.uniform(100, 980)))
        t += 2.0
        recal.add(t, raw_of(target), target)
    return t


def test_constant_drift_is_corrected_gradually():
    recal = ClickRecalibrator(SCREEN)
    drift = lambda p: (p[0] + 120, p[1] - 60)  # the model lands 120 px right, 60 px high
    clicks(recal, 3, drift)
    x, y = recal.correct(*drift((900, 500)))
    assert 950 < x < 990 and 465 < y < 485  # half corrected: three clicks are not trusted fully yet
    clicks(recal, 30, drift, start_t=100, seed=1)
    x, y = recal.correct(*drift((900, 500)))
    assert abs(x - 900) < 15 and abs(y - 500) < 10


def test_scale_error_is_corrected_with_enough_clicks():
    recal = ClickRecalibrator(SCREEN)
    squeeze = lambda p: (960 + (p[0] - 960) * 0.8, 540 + (p[1] - 540) * 0.85)  # estimates too close to centre
    clicks(recal, MIN_FOR_AFFINE + 10, squeeze)
    for target in [(150, 150), (1800, 950), (960, 540)]:
        x, y = recal.correct(*squeeze(target))
        assert math.dist((x, y), target) < 25


def test_clicks_not_looked_at_are_ignored():
    recal = ClickRecalibrator(SCREEN)
    honest = lambda p: (p[0] + 10, p[1])
    t = clicks(recal, 10, honest)
    assert not recal.add(t + 2, (200, 200), (1800, 1000))  # looked elsewhere while clicking
    assert recal.rejected == 1
    assert recal.live_error_px is not None and recal.live_error_px < 20


def test_double_click_counts_once():
    recal = ClickRecalibrator(SCREEN)
    assert recal.add(10.0, (500, 500), (510, 500))
    assert not recal.add(10.1, (500, 500), (510, 500))
    assert recal.clicks_used == 1


def test_old_clicks_fade_when_drift_changes():
    recal = ClickRecalibrator(SCREEN, half_life_s=60)
    t = clicks(recal, 10, lambda p: (p[0] + 100, p[1]))
    clicks(recal, 30, lambda p: (p[0] - 100, p[1]), start_t=t + 600, seed=1)  # ten minutes later, drift flipped
    x, _ = recal.correct(1000, 500)
    assert 1080 < x < 1120  # follows the new drift (+100 correction), not the average of both


def test_tracker_applies_correction_and_keeps_history():
    rng = np.random.default_rng(0)
    feats = rng.uniform(0.3, 0.7, size=(200, 8))
    targets = np.column_stack([feats[:, 0] * 2000, feats[:, 1] * 1000])
    model = GazeModel.fit(feats, targets, screen=SCREEN)
    seen = []
    tracker = GazeTracker(0, on_gaze=seen.append, on_error=lambda m: None)
    tracker.set_model(model, None)
    tracker._publish(1.0, feats[0])
    tracker.set_correction(lambda x, y: (x + 50, y))
    tracker._publish(1.1, feats[0])
    assert abs(seen[1].x - seen[0].x - 50) < 1e-6
    tracker._publish(1.2, None)  # blink: not kept
    assert tracker.recent_features(0.9, 1.15).shape == (2, 8)
    assert tracker.recent_features(1.15, 2.0).shape[0] == 0
    tracker.set_model(model, OneEuroFilter())

import math

import numpy as np

from followeye.config import Calibration, ProviderConfig
from followeye.gaze.landmarks import brighten_if_dark
from followeye.gaze.model import (
    CALIBRATION_VERSION,
    GazeModel,
    OneEuroFilter,
    extract_features,
    filter_for_smoothing,
    head_pose,
    robust_mean_filter,
)
from followeye.models.registry import ProviderRegistry


def pose_matrix(yaw=0.0, z=-60.0):
    m = np.eye(4)
    c, s = math.cos(yaw), math.sin(yaw)
    m[:3, :3] = [[c, 0, s], [0, 1, 0], [-s, 0, c]]
    m[2, 3] = z
    return m


def test_head_pose_reads_yaw_and_distance():
    yaw, pitch, roll, x, y, z = head_pose(pose_matrix(yaw=0.3, z=-55.0))
    assert abs(yaw - 0.3) < 1e-9 and abs(pitch) < 1e-9 and abs(roll) < 1e-9 and z == -55.0


def test_gaze_model_compensates_for_head_and_distance():
    rng = np.random.default_rng(0)
    eyes = rng.uniform(0.3, 0.7, size=(400, 2))
    yaw = rng.uniform(-0.2, 0.2, 400)
    dist = rng.uniform(-75, -45, 400)
    feats = np.column_stack([eyes, yaw, np.zeros((400, 3)), np.zeros(400), dist])
    # Screen position depends on the eyes, the head turn, and (scaled by distance) the eye angle.
    gx = 960 + (eyes[:, 0] - 0.5) * 4000 * (-dist / 60) + yaw * 3000
    gy = 540 + (eyes[:, 1] - 0.5) * 2500 * (-dist / 60)
    targets = np.column_stack([gx, gy])
    model = GazeModel.fit(feats, targets, screen=(0, 0, 1920, 1080))
    assert model.mean_error_px < 40
    restored = GazeModel.from_calibration(model.to_calibration())
    assert restored.to_calibration().version == CALIBRATION_VERSION
    x, y = restored.predict(feats[0])
    assert abs(x - gx[0]) < 60 and abs(y - gy[0]) < 60
    assert model.on_screen(100, 100) and not model.on_screen(5000, 100)


def test_accuracy_report_and_description():
    from followeye.gaze.model import accuracy_report
    from followeye.ui.calibration import describe_accuracy

    rng = np.random.default_rng(3)
    feats = rng.uniform(0.3, 0.7, size=(300, 8))
    model = GazeModel.fit(feats, np.column_stack([feats[:, 0] * 2000, feats[:, 1] * 1000]), screen=(0, 0, 1920, 1080))
    points = []
    for _ in range(5):
        base = rng.uniform(0.35, 0.65, size=8)
        samples = base + rng.normal(0, 0.003, size=(15, 8))
        tx, ty = base[0] * 2000 - 80, base[1] * 1000  # true target 80 px left of where the model lands
        points.append((samples, (tx, ty)))
    report = accuracy_report(model, points)
    assert 70 < report.error_px < 95 and 70 < report.bias_px[0] < 95 and report.jitter_px < 20
    text = describe_accuracy(report, 1920)
    assert "too far right" in text and "Jitter" in text
    assert accuracy_report(model, [(np.empty((0, 8)), (0, 0))]) is None
    assert model.to_calibration(report).test_error_px == report.error_px


def test_old_calibration_files_are_version_1():
    old = Calibration.model_validate({"coefficients": [[0, 0]], "feature_mean": [0], "feature_std": [1],
                                      "screen": [0, 0, 1920, 1080]})
    assert old.version == 1 != CALIBRATION_VERSION


def eye_points():
    lm = np.zeros((478, 2))
    lm[[33, 362]] = [[0.30, 0.40], [0.55, 0.40]]
    lm[[133, 263]] = [[0.45, 0.40], [0.70, 0.40]]
    lm[[159, 386]] = [[0.37, 0.38], [0.62, 0.38]]
    lm[[145, 374]] = [[0.37, 0.42], [0.62, 0.42]]
    lm[[468, 473]] = [[0.375, 0.40], [0.625, 0.40]]
    return lm


def test_extract_features_detects_blink():
    lm = eye_points()
    f = extract_features(lm, pose_matrix(z=-60))
    assert f is not None and len(f) == 8 and abs(f[0] - 0.5) < 1e-6 and f[7] == -60
    lm[[145, 374], 1] = 0.381  # eyelids closed
    assert extract_features(lm, pose_matrix()) is None


def test_robust_mean_filter_drops_outliers():
    rng = np.random.default_rng(2)
    samples = np.vstack([rng.normal(0.5, 0.01, (20, 2)), [[0.9, 0.1], [0.1, 0.9]]])
    kept = robust_mean_filter(samples)
    assert len(kept) == 20


def test_one_euro_smooths_jitter():
    f = OneEuroFilter(min_cutoff=0.5, beta=0.0)
    xs = [f(i / 30, (1000 + (20 if i % 2 else -20), 500))[0] for i in range(60)]
    assert max(xs[30:]) - min(xs[30:]) < 20


def test_default_smoothing_is_steady_but_follows_window_changes():
    rng = np.random.default_rng(0)
    f = filter_for_smoothing(0.5)
    xs = [f(i / 30, (500 + rng.normal(0, 40) if i < 120 else 1300 + rng.normal(0, 40), 300))[0] for i in range(240)]
    assert np.std(xs[60:120]) < 15  # raw noise is 40 px
    assert min(xs[135:]) > 1000  # reached the new window within half a second


def test_brighten_only_dark_frames():
    dark = np.full((120, 160, 3), 30, np.uint8)
    dark[40:80, 60:100] = 60
    out, changed = brighten_if_dark(dark)
    assert changed and out.mean() > dark.mean()
    bright = np.full((120, 160, 3), 160, np.uint8)
    out, changed = brighten_if_dark(bright)
    assert not changed and out is bright


def test_registry_builtins_and_plugins(tmp_path):
    reg = ProviderRegistry()
    ids = {s.cls.provider_id for s in reg.specs()}
    assert {"anthropic", "openai", "ollama", "foundry_local", "onnx_genai", "openai_compatible"} <= ids
    assert reg.create(ProviderConfig(), lambda k: None) is None

    (tmp_path / "echo.py").write_text(
        "from followeye.models.base import ModelProvider, ProviderField\n"
        "class Echo(ModelProvider):\n"
        "    provider_id = 'echo'\n"
        "    display_name = 'Echo'\n"
        "    fields = (ProviderField('word', 'Word', 'text', 'hi'),)\n"
        "    def complete_json(self, request):\n"
        "        return {'ok': self.options['word']}\n"
    )
    (tmp_path / "broken.py").write_text("raise RuntimeError('boom')\n")
    errors = reg.load_plugins(tmp_path)
    assert len(errors) == 1 and "broken.py" in errors[0]
    provider = reg.create(ProviderConfig(provider_id="echo", options={"word": "yo"}), lambda k: None)
    assert provider.test() == 'Connected: {"ok": "yo"}'

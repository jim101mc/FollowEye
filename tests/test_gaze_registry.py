import numpy as np

from followeye.config import ProviderConfig
from followeye.gaze.model import GazeModel, OneEuroFilter, extract_features
from followeye.models.registry import ProviderRegistry


def test_gaze_model_fits_and_roundtrips():
    rng = np.random.default_rng(0)
    feats = rng.uniform(0.3, 0.7, size=(300, 6))
    targets = np.stack([feats[:, 0] * 3000 - 400, feats[:, 1] * 2000 - 300], axis=1)
    model = GazeModel.fit(feats, targets, screen=(0, 0, 1920, 1080))
    assert model.mean_error_px < 5
    x, y = GazeModel.from_calibration(model.to_calibration()).predict(feats[0])
    assert abs(x - targets[0, 0]) < 5 and abs(y - targets[0, 1]) < 5
    assert model.on_screen(100, 100) and not model.on_screen(5000, 100)


def test_extract_features_detects_blink():
    lm = np.zeros((478, 2))
    lm[[33, 362]] = [[0.30, 0.40], [0.55, 0.40]]
    lm[[133, 263]] = [[0.45, 0.40], [0.70, 0.40]]
    lm[[159, 386]] = [[0.37, 0.38], [0.62, 0.38]]
    lm[[145, 374]] = [[0.37, 0.42], [0.62, 0.42]]
    lm[[468, 473]] = [[0.375, 0.40], [0.625, 0.40]]
    lm[1], lm[234], lm[454], lm[10], lm[152] = [0.5, 0.5], [0.2, 0.5], [0.8, 0.5], [0.5, 0.2], [0.5, 0.8]
    f = extract_features(lm)
    assert f is not None and abs(f[0] - 0.5) < 1e-6
    lm[[145, 374], 1] = 0.381  # eyelids closed
    assert extract_features(lm) is None


def test_one_euro_smooths_jitter():
    f = OneEuroFilter(min_cutoff=0.5, beta=0.0)
    xs = [f(i / 30, (1000 + (20 if i % 2 else -20), 500))[0] for i in range(60)]
    assert max(xs[30:]) - min(xs[30:]) < 20


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

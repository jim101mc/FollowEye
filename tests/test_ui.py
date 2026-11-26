import pytest

pytest.importorskip("PySide6")

from cryptography.fernet import Fernet  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402

from followeye.apps import AppInfo  # noqa: E402
from followeye.config import Settings  # noqa: E402
from followeye.models.registry import ProviderRegistry  # noqa: E402
from followeye.modes import Behaviour, Mode, ProgramEntry, Routing, RuleAction, RuleKind, Trigger, WindowRule  # noqa: E402
from followeye.skills.analysis import SkillId  # noqa: E402
from followeye.skills.skills import Suggestion, SuggestionAction  # noqa: E402
from followeye.storage.vault import HistoryLog, Vault  # noqa: E402


class FakeCatalog:
    def installed(self):
        return [AppInfo("excel.exe", "Excel", r"C:\x\excel.exe"), AppInfo("code.exe", "VS Code", r"C:\x\code.exe")]

    def running(self):
        return [AppInfo("slack.exe", "Slack", r"C:\x\slack.exe")]


def wait_for(qapp, condition, timeout_ms=3000):
    from PySide6.QtCore import QDeadlineTimer

    deadline = QDeadlineTimer(timeout_ms)
    while not condition() and not deadline.hasExpired():
        qapp.processEvents()


def test_mode_editor_checklist_override_and_rules(qapp):
    from followeye.ui.mode_editor import ModeEditorDialog

    mode = Mode(name="Reading", programs=[ProgramEntry(exe="code.exe", name="VS Code")])
    dialog = ModeEditorDialog(mode, FakeCatalog(), name_taken=lambda n: False)
    checklist = dialog.checklist
    wait_for(qapp, lambda: checklist.list.count() == 3)
    assert checklist.list.count() == 3  # saved + installed + running, de-duplicated

    items = {checklist.list.item(i).data(Qt.ItemDataRole.UserRole): checklist.list.item(i)
             for i in range(checklist.list.count())}
    items["slack.exe"].setCheckState(Qt.CheckState.Checked)
    checklist._rows["slack.exe"].override = Behaviour(routing=Routing.LOCAL_ONLY)

    dialog.behaviour_editor._checks[Trigger.CLIPBOARD].setChecked(False)
    dialog.rules._add(WindowRule(kind=RuleKind.DOMAIN, pattern="bank.com", action=RuleAction.EXCLUDE))
    dialog._save()
    saved = dialog.mode()
    assert {p.exe for p in saved.programs} == {"code.exe", "slack.exe"}
    slack = saved.program("slack.exe")
    assert slack.override.routing is Routing.LOCAL_ONLY
    assert not saved.behaviour.enabled(Trigger.CLIPBOARD)
    assert saved.rules[0].pattern == "bank.com" and saved.rules[0].action is RuleAction.EXCLUDE


def test_settings_dialog_builds_and_saves(qapp, tmp_path):
    from followeye.ui.settings_dialog import SettingsDialog

    history = HistoryLog(tmp_path / "h.log", Vault(Fernet.generate_key()))
    history.append({"exe": "chrome.exe", "skill": "context", "source": "local", "title": "t", "body": "b",
                    "outcome": "shown"})
    dialog = SettingsDialog(Settings(), ProviderRegistry(), FakeCatalog(), get_secret=lambda n: None,
                            hotkey_free=lambda hk: True, history=history, profile={"Email": "a@b.c"},
                            plugins_dir=tmp_path, plugin_errors=[])
    assert dialog.history_table.rowCount() == 1

    dialog.local_panel.combo.setCurrentIndex(dialog.local_panel.combo.findData("ollama"))
    dialog.local_panel._inputs["model"].setText("llama3.2")
    dialog.cloud_panel.combo.setCurrentIndex(dialog.cloud_panel.combo.findData("anthropic"))
    dialog.cloud_panel._secret_inputs["api_key"].setText("sk-ant-test")

    from PySide6.QtGui import QKeySequence

    dialog.hotkey_edits["pause"].edit.setKeySequence(QKeySequence("Ctrl+Alt+P"))
    dialog._save()
    s = dialog.settings
    assert s.models.local.provider_id == "ollama" and s.models.local.options["model"] == "llama3.2"
    assert s.models.cloud.options["model"] == "claude-opus-5-5"
    assert dialog.pending_secrets() == {"provider.anthropic.api_key": "sk-ant-test"}
    assert s.hotkeys.pause == "Ctrl+Alt+P"
    assert dialog.profile() == {"Email": "a@b.c"}


def test_hotkey_conflicts_are_reported(qapp, tmp_path, monkeypatch):
    from PySide6.QtGui import QKeySequence

    from followeye.ui import settings_dialog
    from followeye.ui.settings_dialog import SettingsDialog

    warnings = []
    monkeypatch.setattr(settings_dialog.QMessageBox, "warning", lambda *a: warnings.append(a[2]))

    dialog = SettingsDialog(Settings(), ProviderRegistry(), FakeCatalog(), lambda n: None,
                            hotkey_free=lambda hk: hk.text != "Ctrl+Alt+K", history=HistoryLog(tmp_path / "h", Vault(Fernet.generate_key())),
                            profile={}, plugins_dir=tmp_path, plugin_errors=[])
    pause = dialog.hotkey_edits["pause"]
    pause.edit.setKeySequence(QKeySequence("Ctrl+Shift+Return"))
    assert "Already used" in pause.error
    pause.edit.setKeySequence(QKeySequence("Ctrl+Alt+K"))
    assert "taken by another program" in pause.error
    pause.edit.setKeySequence(QKeySequence("Shift+K"))
    assert "Ctrl, Alt or Win" in pause.error
    dialog._save()
    assert dialog.result() == 0  # refused to save
    assert "Ctrl, Alt or Win" in warnings[0]


def test_glass_widget_flow(qapp):
    from followeye.config import NotificationSettings
    from followeye.ui.glass_widget import GlassNotification

    widget = GlassNotification(NotificationSettings(), "Ctrl+Shift+Enter", "Ctrl+Shift+Backspace")
    events = []
    widget.action_requested.connect(lambda s, i: events.append(("action", i)))
    widget.closed.connect(lambda s, o: events.append(("closed", o)))
    s = Suggestion(SkillId.CONTEXT, "Title", "Body text", "local",
                   [SuggestionAction("Copy", lambda: "Copied.")], details=["a detail"])
    widget.show_suggestion(s)
    assert widget.isVisible()
    widget.accept_primary()
    assert events == [("action", 0)]
    widget.show_status("Copied.")
    widget.show_suggestion(s)
    widget.dismiss()
    assert events[-1] == ("closed", "dismissed")
    widget.show_info("Mode: Work")


def test_calibration_fit(qapp):
    import numpy as np

    from followeye.ui import calibration as cal

    steps = cal._steps()
    assert len(steps) == 3 * (len(cal.POINTS) + 1) + 1 + len(cal.TEST_POINTS)
    # Every distance round sees every background brightness.
    for r in range(3):
        colors = {s.background.name() for s in steps if s.round == r and s.point is not None}
        assert colors == {c.name() for c in cal.BACKGROUNDS}
    assert steps[-1].round == cal.TEST_ROUND

    window = cal.CalibrationWindow()
    rng = np.random.default_rng(1)
    for r, dist in enumerate((-60.0, -45.0, -75.0)):
        for i, (fx, fy) in enumerate(cal.POINTS):
            base = np.array([fx, fy, 0, 0, 0, 0, 0, dist])
            window._samples[(r, i)] = [base + rng.normal(0, 0.002, 8) for _ in range(20)]
    for i, (fx, fy) in enumerate(cal.TEST_POINTS):
        base = np.array([fx, fy, 0, 0, 0, 0, 0, -60.0])
        window._samples[(cal.TEST_ROUND, i)] = [base + rng.normal(0, 0.002, 8) for _ in range(20)]
    window._index = len(steps)
    window._fit()
    assert window._result is not None and window._test is not None
    assert "On new dots" in window._message and "often lost" not in window._message
    saved = window._result.to_calibration(window._test)
    assert saved.test_error_px is not None and saved.test_jitter_px is not None

    # Test dots never become training data: without them the fit still works, the test is just missing.
    del window._samples[(cal.TEST_ROUND, 0)], window._samples[(cal.TEST_ROUND, 1)], window._samples[(cal.TEST_ROUND, 2)]
    window._fit()
    assert window._test is None and "accuracy test didn't get enough data" in window._message

    # A round where the eyes were lost is pointed out.
    for i in range(len(cal.POINTS)):
        window._samples[(2, i)] = window._samples[(2, i)][:3]
    window._fit()
    assert "“farther” round" in window._message


def test_tray_menu(qapp):
    from followeye.ui.tray import Tray

    tray = Tray()
    tray.icon.setParent(qapp)  # destroyed with the app, not at interpreter exit
    settings = Settings()
    tray.update(settings, paused=False, status="", gaze_dot=True)
    actions = {a.text(): a for a in tray.menu.actions()}
    for mode in settings.modes:
        assert mode.name in actions
    assert actions["Show gaze dot"].isChecked()
    fired = []
    tray.preview_requested.connect(lambda: fired.append("preview"))
    tray.gaze_dot_toggled.connect(lambda: fired.append("dot"))
    actions["Camera preview…"].trigger()
    actions["Show gaze dot"].trigger()
    assert fired == ["preview", "dot"]


def face_points():
    import numpy as np

    lm = np.zeros((478, 2))
    lm[[33, 362]] = [[0.30, 0.40], [0.55, 0.40]]
    lm[[133, 263]] = [[0.45, 0.40], [0.70, 0.40]]
    lm[[159, 386]] = [[0.37, 0.38], [0.62, 0.38]]
    lm[[145, 374]] = [[0.37, 0.42], [0.62, 0.42]]
    lm[[468, 473]] = [[0.375, 0.40], [0.625, 0.40]]
    lm[1], lm[234], lm[454], lm[10], lm[152] = [0.5, 0.5], [0.2, 0.5], [0.8, 0.5], [0.5, 0.2], [0.5, 0.8]
    return lm


def test_camera_preview_annotation(qapp):
    import numpy as np

    from followeye.ui.camera_preview import EYES_CLOSED, NO_FACE, TRACKING, CameraPreview, annotate

    frame = np.zeros((720, 1280, 3), np.uint8)
    rgb, status = annotate(frame, None)
    assert status == NO_FACE and rgb.shape == (270, 480, 3)
    rgb, status = annotate(frame, face_points())
    assert status == TRACKING and rgb.any()  # eye points drawn
    closed = face_points()
    closed[[145, 374], 1] = 0.381
    assert annotate(frame, closed)[1] == EYES_CLOSED
    assert "dark" in annotate(frame, face_points(), brightened=True)[1]

    preview = CameraPreview()
    closed_events = []
    preview.closed.connect(lambda: closed_events.append(True))
    preview.frame.emit(*annotate(frame, face_points()))
    qapp.processEvents()
    assert preview.status.text() == TRACKING and not preview.image.pixmap().isNull()
    preview.show_error("Could not open camera 0.")
    assert preview.image.text() == "Could not open camera 0."
    preview.close()
    assert closed_events == [True]


def test_gaze_dot_follows_and_greys_out(qapp):
    from followeye.ui.gaze_dot import SIZE, GazeDot

    dot = GazeDot()
    dpr = qapp.primaryScreen().devicePixelRatio()
    dot.update_gaze(400 * dpr, 300 * dpr)
    assert dot.isVisible() and not dot.lost
    assert (dot.x() + SIZE // 2, dot.y() + SIZE // 2) == (400, 300)
    dot.update_gaze(None, None)
    assert dot.lost and dot.isVisible()
    dot.update_gaze(500 * dpr, 300 * dpr)
    assert not dot.lost
    dot.hide()

from __future__ import annotations

import hashlib
import logging
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import uiautomation as auto
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from .capture import screenshot, ui_automation
from .capture.activity import ActivityListener
from .capture.desktop import WindowsDesktop
from .capture.ocr import WindowsOcr
from .config import Settings, data_dir, load_settings, save_settings
from .engine.pipeline import Pipeline, ScreenReader
from .engine.triggers import TriggerEngine, TriggerEvent
from .gaze.model import GazeModel, filter_for_smoothing
from .gaze.resolver import WindowResolver
from .gaze.tracker import GazeSample, GazeTracker
from .hotkeys import HotkeyError, validate_hotkey
from .models.base import ModelError
from .models.registry import ProviderRegistry
from .modes import Behaviour, WindowInfo, resolve_behaviour
from .skills.decision import DecisionLayer, EscalationLearner, HourlyBudget
from .skills.skills import Suggestion
from .storage.vault import EncryptedJsonFile, HistoryLog, Vault, get_secret, set_secret
from .ui.calibration import CalibrationWindow
from .ui.glass_widget import GlassNotification
from .ui.settings_dialog import SettingsDialog
from .ui.tray import Tray
from .winapi import windows
from .winapi.apps import WindowsAppCatalog
from .winapi.hotkeys import HotkeyManager, is_hotkey_free

log = logging.getLogger(__name__)

FOREGROUND_POLL_S = 0.1
DUPLICATE_WINDOW_S = 600
EXCLUDED_CLIPBOARD_FORMAT = "ExcludeClipboardContentFromMonitorProcessing"


class _Bridge(QObject):
    """Signals carry results from worker threads to the UI thread."""

    suggestion = Signal(object, object)  # Suggestion, TriggerEvent
    status = Signal(str)
    notify = Signal(str, str)
    action_done = Signal(str)
    copy_text = Signal(str)
    hotkey = Signal(str)
    gaze_error = Signal(str)


class _UiaThread:
    """Single worker thread with UI Automation (COM) initialised."""

    def __init__(self, name: str):
        self._local = threading.local()
        self.executor = ThreadPoolExecutor(1, thread_name_prefix=name, initializer=self._init)

    def _init(self) -> None:
        self._local.uia = auto.UIAutomationInitializerInThread()


class FollowEyeApp(QObject):
    def __init__(self, qapp: QApplication):
        super().__init__()
        self.qapp = qapp
        self.settings: Settings = load_settings()
        home = data_dir()
        self.vault = Vault()
        self.history = HistoryLog(home / "history.log", self.vault, self.settings.privacy.retention_days)
        self.history.purge_old()
        self.profile_store = EncryptedJsonFile(home / "profile.bin", self.vault)
        self.profile: dict[str, str] = self.profile_store.load(default={})
        self.learner_store = EncryptedJsonFile(home / "learner.bin", self.vault)
        self.learner = EscalationLearner.from_dict(self.learner_store.load(default={}))
        self.registry = ProviderRegistry()
        self.plugins_dir = home / "plugins"
        self.plugin_errors = self.registry.load_plugins(self.plugins_dir)
        self.catalog = WindowsAppCatalog()

        self.paused = False
        self.status = ""
        self._mode = self.settings.active_mode()
        self._blocklist = self.settings.privacy.blocklist
        self._last_shown = 0.0
        self._recent: dict[str, float] = {}
        self._history_ids: dict[int, str] = {}
        self._pipeline: Pipeline | None = None
        self._calibration: CalibrationWindow | None = None
        self._settings_dialog: SettingsDialog | None = None

        self.bridge = _Bridge()
        self.bridge.suggestion.connect(self._on_suggestion)
        self.bridge.status.connect(self._set_status)
        self.bridge.notify.connect(lambda title, text: self.tray.message(title, text))
        self.bridge.action_done.connect(lambda text: self.widget.show_status(text))
        self.bridge.copy_text.connect(lambda text: self.qapp.clipboard().setText(text))
        self.bridge.hotkey.connect(self._on_hotkey)
        self.bridge.gaze_error.connect(self._on_gaze_error)

        self.widget = GlassNotification(self.settings.notifications)
        self.widget.action_requested.connect(self._run_action)
        self.widget.closed.connect(self._on_closed)
        self.widget.visibility_changed.connect(self._bind_card_hotkeys)

        self.tray = Tray()
        self.tray.mode_selected.connect(self.set_mode)
        self.tray.pause_toggled.connect(self.toggle_pause)
        self.tray.settings_requested.connect(self.open_settings)
        self.tray.calibrate_requested.connect(self.calibrate)
        self.tray.quit_requested.connect(self.quit)

        self.analysis = _UiaThread("analysis")
        self.actions = _UiaThread("actions")
        self.ocr = WindowsOcr()
        self.desktop = WindowsDesktop(copy_text=self.bridge.copy_text.emit)
        self._pending: TriggerEvent | None = None
        self._busy = False
        self._pending_lock = threading.Lock()

        self.resolver = WindowResolver(self._needs_url, ui_automation.browser_url)
        self.engine = TriggerEngine(self._behaviour_for)
        self._inputs: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._use_foreground = True
        self._engine_thread = threading.Thread(target=self._engine_loop, name="engine", daemon=True)

        self.tracker = GazeTracker(self.settings.gaze.camera_index, self._on_gaze, self.bridge.gaze_error.emit)
        self.activity = ActivityListener(on_key=self._on_key, on_scroll=self._on_scroll)
        self.qapp.clipboard().dataChanged.connect(self._on_clipboard)
        self.hotkeys = HotkeyManager()

    # --- lifecycle ---------------------------------------------------------------

    def start(self) -> None:
        self._engine_thread.start()
        self.activity.start()
        self._apply_settings(first_run=True)

    def quit(self) -> None:
        self._stop.set()
        self.tracker.stop()
        self.activity.stop()
        self.hotkeys.stop()
        self.learner_store.save(self.learner.to_dict())
        self.tray.icon.hide()
        self.qapp.quit()

    def _apply_settings(self, first_run: bool = False) -> None:
        s = self.settings
        self._mode = s.active_mode()
        self._blocklist = s.privacy.blocklist
        self.history.retention_days = s.privacy.retention_days
        self.widget.settings = s.notifications
        self.widget.accept_hint = s.hotkeys.accept
        self.widget.dismiss_hint = s.hotkeys.dismiss
        self._inputs.put(("reset",))
        self._bind_pause_hotkey()
        self._update_tracker()
        threading.Thread(target=self._build_pipeline, name="models", daemon=True).start()
        self._refresh_tray()
        if first_run and s.gaze.enabled and s.gaze.calibration is None:
            self.tray.message("FollowEye is running",
                              "Calibrate eye tracking from the tray icon. Until then FollowEye follows the active window.")

    def _build_pipeline(self) -> None:
        m = self.settings.models
        problems = []
        local = cloud = None
        try:
            local = self.registry.create(m.local, get_secret)
        except ModelError as e:
            problems.append(f"Local model: {e}")
        try:
            cloud = self.registry.create(m.cloud, get_secret)
        except ModelError as e:
            problems.append(f"Cloud model: {e}")
        reader = ScreenReader(
            window_rect=windows.window_rect,
            grab=screenshot.grab,
            ui_snapshot=self._ui_snapshot,
            ocr=self.ocr.recognize,
            to_png=screenshot.to_png,
        )
        decision = DecisionLayer(m.escalation_confidence, self.learner, HourlyBudget(m.max_cloud_requests_per_hour))
        self._pipeline = Pipeline(reader, self.desktop, decision, m, local, cloud,
                                  profile=lambda: self.profile,
                                  show_threshold=self.settings.notifications.show_threshold)
        if local is None and cloud is None and not problems:
            problems.append("No model chosen yet: open Settings → Models.")
        self.bridge.status.emit(problems[0] if problems else "")
        if problems:
            self.bridge.notify.emit("FollowEye models", "\n".join(problems))

    @staticmethod
    def _ui_snapshot(hwnd: int):
        snap = ui_automation.snapshot(hwnd)
        return snap.text, snap.fields

    # --- modes, pause, hotkeys -----------------------------------------------------

    def set_mode(self, mode_id: str) -> None:
        if self.settings.mode(mode_id) is None:
            return
        self.settings.active_mode_id = mode_id
        save_settings(self.settings)
        self._mode = self.settings.active_mode()
        self._inputs.put(("reset",))
        self._update_tracker()
        self._refresh_tray()
        self.widget.show_info(f"Mode: {self._mode.name}")

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        self._inputs.put(("reset",))
        self._update_tracker()
        self._refresh_tray()
        self.widget.show_info("FollowEye paused" if self.paused else f"FollowEye resumed · {self._mode.name}")

    def _bind_pause_hotkey(self) -> None:
        try:
            hotkey = validate_hotkey(self.settings.hotkeys.pause)
        except HotkeyError as e:
            self.tray.message("Pause hotkey", str(e))
            return
        if not self.hotkeys.bind("pause", hotkey, lambda: self.bridge.hotkey.emit("pause")):
            self.tray.message("Pause hotkey", f"{hotkey.text} is taken by another program; choose another in Settings.")

    def _bind_card_hotkeys(self, visible: bool) -> None:
        """Accept/dismiss hotkeys exist only while a suggestion is on screen."""
        for name in ("accept", "dismiss"):
            if not visible:
                self.hotkeys.unbind(name)
                continue
            try:
                hotkey = validate_hotkey(getattr(self.settings.hotkeys, name))
            except HotkeyError:
                continue
            self.hotkeys.bind(name, hotkey, lambda name=name: self.bridge.hotkey.emit(name))

    def _on_hotkey(self, name: str) -> None:
        if name == "pause":
            self.toggle_pause()
        elif name == "accept":
            self.widget.accept_primary()
        elif name == "dismiss":
            self.widget.dismiss()

    def _refresh_tray(self) -> None:
        self.tray.update(self.settings, self.paused, self.status)

    def _set_status(self, text: str) -> None:
        self.status = text
        self._refresh_tray()

    # --- observation inputs (various threads) ----------------------------------------

    def _observing(self) -> bool:
        return not self.paused and self._mode.observe

    def _needs_url(self) -> bool:
        return self._mode.needs_url or self._blocklist.needs_url

    def _behaviour_for(self, window: WindowInfo) -> Behaviour | None:
        return resolve_behaviour(self._mode, self._blocklist, window)

    def _on_gaze(self, sample: GazeSample) -> None:
        if self._observing():
            self._inputs.put(("gaze", sample))

    def _on_key(self) -> None:
        if self._observing():
            self._inputs.put(("key", time.monotonic()))

    def _on_scroll(self, x: int, y: int) -> None:
        if self._observing():
            self._inputs.put(("scroll", time.monotonic(), x, y))

    def _on_clipboard(self) -> None:
        if not self._observing():
            return
        mime = self.qapp.clipboard().mimeData()
        if mime is None or any(EXCLUDED_CLIPBOARD_FORMAT in f for f in mime.formats()):
            return  # password managers mark their copies this way
        text = self.qapp.clipboard().text()
        if text.strip():
            self._inputs.put(("clip", time.monotonic(), text[:2000]))

    def _update_tracker(self) -> None:
        g = self.settings.gaze
        run = g.enabled and g.calibration is not None and self._observing()
        if self._calibration is not None:
            return  # calibration owns the camera
        if run:
            self.tracker.camera_index = g.camera_index
            self.tracker.set_model(GazeModel.from_calibration(g.calibration), filter_for_smoothing(g.smoothing))
            self.tracker.start()
        else:
            self.tracker.stop()
        self._use_foreground = not run

    def _on_gaze_error(self, message: str) -> None:
        self._use_foreground = True
        self._set_status("Eye tracking off: " + message)
        self.tray.message("Eye tracking unavailable", message + " Following the active window instead.")

    # --- engine thread ---------------------------------------------------------------

    def _engine_loop(self) -> None:
        with auto.UIAutomationInitializerInThread():
            last_poll = 0.0
            while not self._stop.is_set():
                try:
                    item = self._inputs.get(timeout=0.05)
                except queue.Empty:
                    item = None
                now = time.monotonic()
                poll = now - last_poll >= FOREGROUND_POLL_S
                if poll:
                    last_poll = now
                try:
                    self._engine_step(item, now, poll)
                except Exception:  # keep observing even if one window misbehaves
                    log.exception("Engine step failed")

    def _engine_step(self, item, now: float, poll_foreground: bool) -> None:
        events: list[TriggerEvent] = []
        if item is not None:
            kind = item[0]
            if kind == "reset":
                self.engine.reset()
            elif kind == "gaze" and not self._use_foreground:
                sample: GazeSample = item[1]
                window = self.resolver.at_point(sample.x, sample.y) if sample.x is not None else None
                events += self.engine.on_gaze(sample.t, window)
            elif kind == "key":
                events += self.engine.on_key(item[1], self.resolver.foreground())
            elif kind == "scroll":
                events += self.engine.on_scroll(item[1], self.resolver.at_point(item[2], item[3]))
            elif kind == "clip":
                events += self.engine.on_clipboard(item[1], self.resolver.foreground(), item[2])
        if self._use_foreground and self._observing() and poll_foreground:
            events += self.engine.on_gaze(now, self.resolver.foreground())
        if self._observing():
            events += self.engine.tick(now)
        for event in events:
            self._submit(event)

    # --- analysis thread -------------------------------------------------------------

    def _submit(self, event: TriggerEvent) -> None:
        with self._pending_lock:
            self._pending = event  # only the newest trigger matters
            if self._busy:
                return
            self._busy = True
        self.analysis.executor.submit(self._drain)

    def _drain(self) -> None:
        while True:
            with self._pending_lock:
                event, self._pending = self._pending, None
                if event is None:
                    self._busy = False
                    return
            pipeline = self._pipeline
            if pipeline is None or not self._observing():
                continue
            try:
                suggestion = pipeline.handle(event)
            except Exception:  # a failing window or model must not stop future analysis
                log.exception("Analysis failed")
                continue
            if (pipeline.last_error or "") != self.status:
                self.bridge.status.emit(pipeline.last_error or "")
            if suggestion is not None:
                self.bridge.suggestion.emit(suggestion, event)

    # --- suggestions (UI thread) -------------------------------------------------------

    def _on_suggestion(self, suggestion: Suggestion, event: TriggerEvent) -> None:
        if not self._observing():
            return
        now = time.monotonic()
        if now - self._last_shown < self.settings.notifications.cooldown_seconds:
            return
        key = hashlib.sha256(f"{suggestion.title}\x00{suggestion.body}".encode()).hexdigest()
        self._recent = {k: t for k, t in self._recent.items() if now - t < DUPLICATE_WINDOW_S}
        if key in self._recent:
            return
        self._recent[key] = now
        self._last_shown = now
        if self.settings.privacy.logging_enabled:
            self._history_ids[id(suggestion)] = self.history.append({
                "mode": self._mode.name,
                "exe": event.window.exe,
                "window": event.window.title,
                "trigger": event.trigger.value,
                "skill": suggestion.skill.value,
                "source": suggestion.source,
                "title": suggestion.title,
                "body": suggestion.body,
                "details": suggestion.details,
                "outcome": "shown",
            })
        self.widget.show_suggestion(suggestion)

    def _run_action(self, suggestion: Suggestion, index: int) -> None:
        action = suggestion.actions[index]

        def work():
            try:
                message = action.run() or "Done."
            except Exception as e:  # file gone, window closed, etc.
                log.exception("Action failed")
                message = f"Couldn't do that: {e}"
            self.bridge.action_done.emit(message)

        self.actions.executor.submit(work)
        self._record_outcome(suggestion, f"accepted: {action.label}", accepted=True)

    def _on_closed(self, suggestion: Suggestion, outcome: str) -> None:
        self._record_outcome(suggestion, outcome, accepted=False if outcome == "dismissed" else None)

    def _record_outcome(self, suggestion: Suggestion, outcome: str, accepted: bool | None) -> None:
        record_id = self._history_ids.pop(id(suggestion), None)
        if record_id is not None:
            self.history.update(record_id, outcome=outcome)
        if accepted is not None and suggestion.features is not None:
            self.learner.add(suggestion.features, accepted)
            self.learner_store.save(self.learner.to_dict())

    # --- dialogs ------------------------------------------------------------------------

    def open_settings(self, tab: str = "modes") -> None:
        if self._settings_dialog is not None:
            self._settings_dialog.open_tab(tab)
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            return
        dialog = SettingsDialog(
            self.settings, self.registry, self.catalog, get_secret, is_hotkey_free, self.history,
            self.profile, self.plugins_dir, self.plugin_errors,
        )
        dialog.calibrate_requested.connect(self.calibrate)
        dialog.open_tab(tab)
        self._settings_dialog = dialog
        dialog.finished.connect(lambda result: self._settings_closed(dialog, result))
        dialog.show()
        dialog.activateWindow()

    def _settings_closed(self, dialog: SettingsDialog, result: int) -> None:
        self._settings_dialog = None
        if not result:
            return
        for name, value in dialog.pending_secrets().items():
            set_secret(name, value)
        self.profile = dialog.profile()
        self.profile_store.save(self.profile)
        self.settings = dialog.settings
        save_settings(self.settings)
        self._apply_settings()

    def calibrate(self) -> None:
        if self._calibration is not None:
            return
        self.tracker.stop()
        window = CalibrationWindow()
        self._calibration = window
        self.tracker.camera_index = self.settings.gaze.camera_index
        self.tracker.set_model(None, None)
        self.tracker.set_feature_listener(window.feature.emit)
        window.finished.connect(self._calibration_finished)
        self.tracker.start()
        window.start()

    def _calibration_finished(self, result) -> None:
        self.tracker.set_feature_listener(None)
        self.tracker.stop()
        self._calibration = None
        if result is not None:
            self.settings.gaze.calibration = result
            self.settings.gaze.enabled = True
            save_settings(self.settings)
            self.tray.message("Calibration saved", f"Average error {result.mean_error_px:.0f} px.")
        self._update_tracker()

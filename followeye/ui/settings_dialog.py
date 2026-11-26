from __future__ import annotations

import datetime
import threading
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..apps import AppCatalog
from ..config import HOTKEY_LABELS, NotificationSettings, ProviderConfig, Settings, WidgetCorner
from ..models.base import ModelError, ProviderField, secret_name
from ..models.registry import PLUGIN_HELP, ProviderRegistry
from ..modes import Mode, preset_modes
from ..storage.vault import HistoryLog
from .editors import HotkeyEdit, HotkeyFreeCheck, RulesEditor
from .program_picker import ProgramChecklist
from .mode_editor import ModeEditorDialog

TABS = ("modes", "models", "notifications", "privacy", "profile", "gaze", "history")
CORNER_LABELS = {
    WidgetCorner.BOTTOM_RIGHT: "Bottom right",
    WidgetCorner.TOP_RIGHT: "Top right",
    WidgetCorner.BOTTOM_LEFT: "Bottom left",
    WidgetCorner.TOP_LEFT: "Top left",
}


class _TestRunner(QObject):
    done = Signal(str)

    def run(self, fn: Callable[[], str]) -> None:
        def work():
            try:
                self.done.emit(fn())
            except ModelError as e:
                self.done.emit(f"Failed: {e}")
            except Exception as e:  # surface any provider bug to the user instead of crashing
                self.done.emit(f"Failed: {type(e).__name__}: {e}")

        threading.Thread(target=work, daemon=True).start()


class ProviderPanel(QGroupBox):
    """Pick a model provider and fill in its options; secret fields go to Credential Manager."""

    def __init__(self, title: str, registry: ProviderRegistry, config: ProviderConfig,
                 get_secret: Callable[[str], str | None], parent=None):
        super().__init__(title, parent)
        self.registry = registry
        self._get_secret = get_secret
        self.secret_changes: dict[str, str | None] = {}
        self._inputs: dict[str, QWidget] = {}
        self._secret_inputs: dict[str, QLineEdit] = {}
        self._forget: set[str] = set()

        layout = QVBoxLayout(self)
        self.combo = QComboBox()
        self.combo.addItem("None", "")
        for spec in registry.specs():
            suffix = "" if spec.source == "built-in" else f"  (plugin: {spec.source})"
            self.combo.addItem(spec.cls.display_name + suffix, spec.cls.provider_id)
        layout.addWidget(self.combo)
        self.description = QLabel(wordWrap=True)
        layout.addWidget(self.description)
        self.form_host = QWidget()
        self.form = QFormLayout(self.form_host)
        self.form.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.form_host)
        test_row = QHBoxLayout()
        self.test_button = QPushButton("Test connection")
        self.test_result = QLabel(wordWrap=True)
        test_row.addWidget(self.test_button)
        test_row.addWidget(self.test_result, 1)
        layout.addLayout(test_row)

        self._initial = config
        self.combo.setCurrentIndex(max(0, self.combo.findData(config.provider_id)))
        self.combo.currentIndexChanged.connect(lambda _i: self._build(ProviderConfig(provider_id=self.combo.currentData())))
        self._runner = _TestRunner()
        self._runner.done.connect(self._test_done)
        self.test_button.clicked.connect(self._test)
        self._build(config)

    def _build(self, config: ProviderConfig) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)
        self._inputs.clear()
        self._secret_inputs.clear()
        self._forget.clear()
        self.test_result.clear()
        spec = self.registry.get(config.provider_id)
        self.test_button.setEnabled(spec is not None)
        self.description.setText(spec.cls.description if spec else "No model: this stage is skipped.")
        if spec is None:
            return
        for f in spec.cls.fields:
            self.form.addRow(f.label, self._field_widget(spec.cls.provider_id, f, config.options.get(f.key, f.default)))

    def _field_widget(self, provider_id: str, f: ProviderField, value) -> QWidget:
        if f.kind == "secret":
            edit = QLineEdit(echoMode=QLineEdit.EchoMode.Password)
            saved = self._get_secret(secret_name(provider_id, f.key)) is not None
            edit.setPlaceholderText("Saved – type to replace" if saved else (f.placeholder or "Not set"))
            edit.setToolTip(f.help or "Stored in Windows Credential Manager, never in the settings file.")
            self._secret_inputs[f.key] = edit
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.addWidget(edit, 1)
            if saved:
                forget = QToolButton(text="Forget", toolTip="Delete the saved key")
                forget.clicked.connect(lambda: (self._forget.add(f.key), edit.clear(), edit.setPlaceholderText("Will be deleted")))
                h.addWidget(forget)
            return row
        if f.kind == "bool":
            widget = QCheckBox()
            widget.setChecked(bool(value))
        elif f.kind == "int":
            widget = QSpinBox(minimum=1, maximum=1_000_000)
            widget.setValue(int(value or 0))
        elif f.kind == "choice":
            widget = QComboBox()
            widget.addItems(list(f.choices))
            widget.setCurrentText(str(value))
        elif f.kind == "path":
            edit = QLineEdit(str(value or ""))
            browse = QPushButton("Browse…")
            browse.clicked.connect(lambda: edit.setText(QFileDialog.getExistingDirectory(self, f.label, edit.text()) or edit.text()))
            widget = QWidget()
            h = QHBoxLayout(widget)
            h.setContentsMargins(0, 0, 0, 0)
            h.addWidget(edit, 1)
            h.addWidget(browse)
            self._inputs[f.key] = edit
            if f.help:
                widget.setToolTip(f.help)
            return widget
        else:
            widget = QLineEdit(str(value or ""))
            widget.setPlaceholderText(f.placeholder)
        if f.help:
            widget.setToolTip(f.help)
        self._inputs[f.key] = widget
        return widget

    def refresh(self) -> None:
        self._build(self.config())

    def config(self) -> ProviderConfig:
        provider_id = self.combo.currentData()
        options = {}
        for key, w in self._inputs.items():
            if isinstance(w, QCheckBox):
                options[key] = w.isChecked()
            elif isinstance(w, QSpinBox):
                options[key] = w.value()
            elif isinstance(w, QComboBox):
                options[key] = w.currentText()
            else:
                options[key] = w.text().strip()
        return ProviderConfig(provider_id=provider_id, options=options)

    def pending_secrets(self) -> dict[str, str | None]:
        provider_id = self.combo.currentData()
        out: dict[str, str | None] = {secret_name(provider_id, k): None for k in self._forget}
        for key, edit in self._secret_inputs.items():
            if edit.text().strip():
                out[secret_name(provider_id, key)] = edit.text().strip()
        return out

    def _test(self) -> None:
        config = self.config()
        pending = self.pending_secrets()

        def secrets(name: str) -> str | None:
            return pending[name] if name in pending else self._get_secret(name)

        self.test_button.setEnabled(False)
        self.test_result.setText("Testing… (a local model may take a while to load)")
        self._runner.run(lambda: self.registry.create(config, secrets).test())

    def _test_done(self, text: str) -> None:
        self.test_button.setEnabled(True)
        self.test_result.setText(text)


class SettingsDialog(QDialog):
    calibrate_requested = Signal()
    saved = Signal()  # settings, secrets and profile are ready to apply; the window stays open
    test_notification_requested = Signal(object)  # NotificationSettings as currently set in the window

    def __init__(
        self,
        settings: Settings,
        registry: ProviderRegistry,
        catalog: AppCatalog,
        get_secret: Callable[[str], str | None],
        hotkey_free: HotkeyFreeCheck,
        history: HistoryLog,
        profile: dict[str, str],
        plugins_dir: Path,
        plugin_errors: list[str],
        gaze_stats: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("FollowEye settings")
        self.resize(760, 680)
        self.settings = settings.model_copy(deep=True)
        self.registry = registry
        self.catalog = catalog
        self.history = history
        self._profile = dict(profile)
        self._plugins_dir = plugins_dir
        self._gaze_stats = gaze_stats

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.tabs.addTab(self._modes_tab(), "Modes")
        self.tabs.addTab(self._models_tab(get_secret, plugin_errors), "Models")
        self.tabs.addTab(self._notifications_tab(hotkey_free), "Notifications && hotkeys")
        self.tabs.addTab(self._privacy_tab(), "Privacy")
        self.tabs.addTab(self._profile_tab(), "Profile")
        self.tabs.addTab(self._gaze_tab(), "Eye tracking")
        self.tabs.addTab(self._history_tab(), "History")

        footer = QHBoxLayout()
        self.save_status = QLabel()
        footer.addWidget(self.save_status, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)  # discards changes not saved yet
        footer.addWidget(buttons)
        layout.addLayout(footer)
        self._status_timer = QTimer(self, singleShot=True, interval=2500, timeout=self.save_status.clear)

    def open_tab(self, name: str) -> None:
        if name in TABS:
            self.tabs.setCurrentIndex(TABS.index(name))

    # --- Modes ----------------------------------------------------------------

    def _modes_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.addWidget(QLabel("A mode decides which programs FollowEye watches and how it reacts. "
                                "Switch modes from the tray icon."))
        row = QHBoxLayout()
        self.mode_list = QListWidget()
        self.mode_list.itemDoubleClicked.connect(lambda _i: self._edit_mode())
        row.addWidget(self.mode_list, 1)
        side = QVBoxLayout()
        for label, slot in (
            ("New mode…", self._new_mode),
            ("Edit…", self._edit_mode),
            ("Duplicate", self._duplicate_mode),
            ("Delete", self._delete_mode),
            ("Make active", self._activate_mode),
            ("Restore presets", self._restore_presets),
        ):
            button = QPushButton(label)
            button.clicked.connect(slot)
            side.addWidget(button)
        side.addStretch(1)
        row.addLayout(side)
        layout.addLayout(row)
        self._refresh_modes()
        return tab

    def _refresh_modes(self, select: str | None = None) -> None:
        self.mode_list.clear()
        for mode in self.settings.modes:
            tags = []
            if mode.id == self.settings.active_mode_id:
                tags.append("active")
            if mode.preset:
                tags.append("preset")
            if mode.locked:
                tags.append("locked")
            item = QListWidgetItem(mode.name + (f"   ({', '.join(tags)})" if tags else ""))
            item.setData(Qt.ItemDataRole.UserRole, mode.id)
            self.mode_list.addItem(item)
            if mode.id == select:
                self.mode_list.setCurrentItem(item)

    def _selected_mode(self) -> Mode | None:
        item = self.mode_list.currentItem()
        return self.settings.mode(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def _name_taken(self, exclude_id: str | None) -> Callable[[str], bool]:
        return lambda name: any(m.name.lower() == name.lower() and m.id != exclude_id for m in self.settings.modes)

    def _open_editor(self, mode: Mode, is_new: bool) -> None:
        dialog = ModeEditorDialog(mode, self.catalog, self._name_taken(None if is_new else mode.id), self)
        if not dialog.exec():
            return
        edited = dialog.mode()
        if is_new:
            self.settings.modes.append(edited)
        else:
            self.settings.modes = [edited if m.id == edited.id else m for m in self.settings.modes]
        self._refresh_modes(select=edited.id)

    def _new_mode(self) -> None:
        self._open_editor(Mode(name=self._unique_name("New mode")), is_new=True)

    def _edit_mode(self) -> None:
        mode = self._selected_mode()
        if mode is None:
            return
        if mode.locked:
            QMessageBox.information(self, "Mode", f"“{mode.name}” turns observation off and can't be edited.")
            return
        self._open_editor(mode, is_new=False)

    def _duplicate_mode(self) -> None:
        mode = self._selected_mode()
        if mode is None:
            return
        copy = mode.model_copy(deep=True, update={"name": self._unique_name(f"{mode.name} copy"), "preset": None, "locked": False})
        copy.id = Mode(name="x").id
        self.settings.modes.append(copy)
        self._refresh_modes(select=copy.id)

    def _delete_mode(self) -> None:
        mode = self._selected_mode()
        if mode is None:
            return
        if mode.locked:
            QMessageBox.information(self, "Mode", f"“{mode.name}” can't be deleted.")
            return
        if QMessageBox.question(self, "Delete mode", f"Delete “{mode.name}”?") != QMessageBox.StandardButton.Yes:
            return
        self.settings.modes = [m for m in self.settings.modes if m.id != mode.id]
        if self.settings.active_mode_id == mode.id:
            self.settings.active_mode_id = self.settings.modes[0].id
        self._refresh_modes()

    def _activate_mode(self) -> None:
        mode = self._selected_mode()
        if mode is not None:
            self.settings.active_mode_id = mode.id
            self._refresh_modes(select=mode.id)

    def _restore_presets(self) -> None:
        if QMessageBox.question(self, "Restore presets",
                                "Reset Full, Work, Focus and Off to their original settings? Your own modes are kept.") \
                != QMessageBox.StandardButton.Yes:
            return
        presets = {m.id: m for m in preset_modes()}
        kept = [m for m in self.settings.modes if m.id not in presets]
        self.settings.modes = list(presets.values()) + kept
        self._refresh_modes()

    def _unique_name(self, base: str) -> str:
        names = {m.name.lower() for m in self.settings.modes}
        name, n = base, 2
        while name.lower() in names:
            name, n = f"{base} {n}", n + 1
        return name

    # --- Models -----------------------------------------------------------------

    def _models_tab(self, get_secret, plugin_errors: list[str]) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        m = self.settings.models
        self.local_panel = ProviderPanel("Local model (first-stage analysis)", self.registry, m.local, get_secret)
        self.cloud_panel = ProviderPanel("Cloud model (advanced reasoning)", self.registry, m.cloud, get_secret)
        layout.addWidget(self.local_panel)
        layout.addWidget(self.cloud_panel)

        group = QGroupBox("Escalation and privacy")
        form = QFormLayout(group)
        self.escalation = QDoubleSpinBox(minimum=0.0, maximum=1.0, singleStep=0.05, decimals=2)
        self.escalation.setValue(m.escalation_confidence)
        self.escalation.setToolTip("Below this local confidence, the cloud model is asked (when the mode allows it).")
        form.addRow("Escalate below confidence", self.escalation)
        self.max_cloud = QSpinBox(minimum=0, maximum=10_000)
        self.max_cloud.setValue(m.max_cloud_requests_per_hour)
        form.addRow("Max cloud requests per hour", self.max_cloud)
        self.send_screens = QCheckBox("Send screenshots to the cloud model (otherwise only text)")
        self.send_screens.setChecked(m.send_screenshots_to_cloud)
        form.addRow(self.send_screens)
        self.share_profile = QCheckBox("Share my profile with the cloud model (for form filling)")
        self.share_profile.setChecked(m.share_profile_with_cloud)
        form.addRow(self.share_profile)
        layout.addWidget(group)

        plugins = QHBoxLayout()
        open_plugins = QPushButton("Open plugins folder")
        open_plugins.setToolTip(PLUGIN_HELP)
        open_plugins.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._plugins_dir))))
        plugins.addWidget(open_plugins)
        note = QLabel("Add your own model as a plugin. " + ("Plugin errors: " + "; ".join(plugin_errors) if plugin_errors else ""))
        note.setWordWrap(True)
        plugins.addWidget(note, 1)
        layout.addLayout(plugins)
        layout.addStretch(1)
        return tab

    # --- Notifications & hotkeys ------------------------------------------------

    def _notifications_tab(self, hotkey_free: HotkeyFreeCheck) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        n = self.settings.notifications
        group = QGroupBox("Notification widget")
        form = QFormLayout(group)
        self.corner = QComboBox()
        for corner, label in CORNER_LABELS.items():
            self.corner.addItem(label, corner)
        self.corner.setCurrentIndex(list(CORNER_LABELS).index(n.corner))
        form.addRow("Position", self.corner)
        self.auto_hide = QSpinBox(minimum=3, maximum=120, suffix=" s")
        self.auto_hide.setValue(n.auto_hide_seconds)
        form.addRow("Hide after", self.auto_hide)
        self.cooldown = QSpinBox(minimum=0, maximum=3600, suffix=" s")
        self.cooldown.setValue(n.cooldown_seconds)
        form.addRow("Minimum time between suggestions", self.cooldown)
        self.show_threshold = QDoubleSpinBox(minimum=0.0, maximum=1.0, singleStep=0.05, decimals=2)
        self.show_threshold.setValue(n.show_threshold)
        self.show_threshold.setToolTip("Suggestions the model is less sure about are not shown.")
        form.addRow("Only show above confidence", self.show_threshold)
        test = QPushButton("Send test notification")
        test.setToolTip("Shows a sample suggestion with the position and timing set above, even before saving.")
        test.clicked.connect(lambda: self.test_notification_requested.emit(self.notification_settings()))
        form.addRow(test)
        layout.addWidget(group)

        hk_group = QGroupBox("Global hotkeys")
        hk_form = QFormLayout(hk_group)
        current = self.settings.hotkeys.model_dump()
        owned = set(current.values())
        self.hotkey_edits: dict[str, HotkeyEdit] = {}
        for key, label in HOTKEY_LABELS.items():
            edit = HotkeyEdit(current[key], hotkey_free, owned)
            self.hotkey_edits[key] = edit
            hk_form.addRow(label, edit)
        for key, edit in self.hotkey_edits.items():
            edit.set_conflict_source(
                lambda key=key: {HOTKEY_LABELS[k]: e.text() for k, e in self.hotkey_edits.items() if k != key}
            )
            edit.changed.connect(lambda key=key: [e.revalidate() for k, e in self.hotkey_edits.items() if k != key])
        layout.addWidget(hk_group)
        layout.addStretch(1)
        return tab

    # --- Privacy ----------------------------------------------------------------

    def _privacy_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        p = self.settings.privacy
        block = QGroupBox("Blocklist – never captured, in any mode")
        block_layout = QVBoxLayout(block)
        inner = QTabWidget()
        self.block_programs = ProgramChecklist(self.catalog, p.blocklist.programs)
        inner.addTab(self.block_programs, "Programs")
        self.block_rules = RulesEditor(p.blocklist.rules, with_action=False)
        inner.addTab(self.block_rules, "Websites && titles")
        block_layout.addWidget(inner)
        layout.addWidget(block, 1)

        log_group = QGroupBox("Encrypted history log")
        form = QFormLayout(log_group)
        self.logging = QCheckBox("Keep a local, encrypted log of suggestions")
        self.logging.setChecked(p.logging_enabled)
        form.addRow(self.logging)
        self.retention = QSpinBox(minimum=1, maximum=3650, suffix=" days")
        self.retention.setValue(p.retention_days)
        form.addRow("Delete entries after", self.retention)
        layout.addWidget(log_group)
        return tab

    # --- Profile ----------------------------------------------------------------

    def _profile_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        info = QLabel("Details FollowEye may use to fill in forms, e.g. “Full name”, “Email”, “Street”. "
                      "Stored encrypted on this PC. Sent to the cloud model only if allowed on the Models tab. "
                      "Never add passwords or card numbers.")
        info.setWordWrap(True)
        layout.addWidget(info)
        self.profile_table = QTableWidget(0, 2)
        self.profile_table.setHorizontalHeaderLabels(["Field", "Value"])
        self.profile_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.profile_table.verticalHeader().hide()
        for k, v in self._profile.items():
            self._add_profile_row(k, v)
        layout.addWidget(self.profile_table, 1)
        row = QHBoxLayout()
        add = QPushButton("Add")
        add.clicked.connect(lambda: self._add_profile_row("", ""))
        remove = QPushButton("Remove selected")
        remove.clicked.connect(lambda: [self.profile_table.removeRow(r) for r in
                                        sorted({i.row() for i in self.profile_table.selectedIndexes()}, reverse=True)])
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch(1)
        layout.addLayout(row)
        return tab

    def _add_profile_row(self, key: str, value: str) -> None:
        r = self.profile_table.rowCount()
        self.profile_table.insertRow(r)
        self.profile_table.setItem(r, 0, QTableWidgetItem(key))
        self.profile_table.setItem(r, 1, QTableWidgetItem(value))

    def profile(self) -> dict[str, str]:
        out = {}
        for r in range(self.profile_table.rowCount()):
            key = (self.profile_table.item(r, 0) or QTableWidgetItem()).text().strip()
            value = (self.profile_table.item(r, 1) or QTableWidgetItem()).text().strip()
            if key and value:
                out[key] = value
        return out

    # --- Eye tracking -------------------------------------------------------------

    def _gaze_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        g = self.settings.gaze
        form = QFormLayout()
        self.gaze_enabled = QCheckBox("Use the webcam to follow where I look")
        self.gaze_enabled.setChecked(g.enabled)
        form.addRow(self.gaze_enabled)
        self.camera = QSpinBox(minimum=0, maximum=9)
        self.camera.setValue(g.camera_index)
        form.addRow("Camera number", self.camera)
        self.smoothing = QDoubleSpinBox(minimum=0.0, maximum=1.0, singleStep=0.1, decimals=1)
        self.smoothing.setValue(g.smoothing)
        self.smoothing.setToolTip("Higher is steadier but slower to follow quick eye movements.")
        form.addRow("Smoothing", self.smoothing)
        layout.addLayout(form)
        if g.calibration:
            status = "Calibrated."
            if self._gaze_stats:
                status += "\n" + self._gaze_stats + "\n"
        else:
            status = "Not calibrated yet. Until then FollowEye follows the active window instead of your eyes."
        label = QLabel(status + " Calibrate on your main display, sitting as you normally do.")
        label.setWordWrap(True)
        layout.addWidget(label)
        calibrate = QPushButton("Calibrate now…")
        calibrate.clicked.connect(self._request_calibration)
        layout.addWidget(calibrate, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)
        return tab

    def _request_calibration(self) -> None:
        self.calibrate_requested.emit()
        self.reject()

    # --- History ------------------------------------------------------------------

    def _history_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.history_table = QTableWidget(0, 6)
        self.history_table.setHorizontalHeaderLabels(["Time", "Program", "Kind", "Model", "Suggestion", "Outcome"])
        self.history_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.history_table.verticalHeader().hide()
        self.history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self.history_table, 1)
        row = QHBoxLayout()
        for label, slot in (("Refresh", self._load_history), ("Delete selected", self._delete_history),
                            ("Delete all", self._clear_history)):
            button = QPushButton(label)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        self._load_history()
        return tab

    def _load_history(self) -> None:
        records = sorted(self.history.records(), key=lambda r: r["time"], reverse=True)
        self.history_table.setRowCount(len(records))
        for r, rec in enumerate(records):
            when = datetime.datetime.fromtimestamp(rec["time"]).strftime("%Y-%m-%d %H:%M")
            values = [when, rec.get("exe", ""), rec.get("skill", ""), rec.get("source", ""),
                      f"{rec.get('title', '')} – {rec.get('body', '')}", rec.get("outcome", "")]
            for c, v in enumerate(values):
                item = QTableWidgetItem(str(v))
                item.setData(Qt.ItemDataRole.UserRole, rec["id"])
                self.history_table.setItem(r, c, item)

    def _delete_history(self) -> None:
        ids = {self.history_table.item(i.row(), 0).data(Qt.ItemDataRole.UserRole)
               for i in self.history_table.selectedIndexes()}
        if ids:
            self.history.delete(ids)
            self._load_history()

    def _clear_history(self) -> None:
        if QMessageBox.question(self, "History", "Delete the whole history log?") == QMessageBox.StandardButton.Yes:
            self.history.clear()
            self._load_history()

    # --- Save -----------------------------------------------------------------------

    def notification_settings(self) -> NotificationSettings:
        return NotificationSettings(
            corner=self.corner.currentData(),
            auto_hide_seconds=self.auto_hide.value(),
            cooldown_seconds=self.cooldown.value(),
            show_threshold=self.show_threshold.value(),
        )

    def _save(self) -> None:
        errors = [f"{HOTKEY_LABELS[k]}: {e.error}" for k, e in self.hotkey_edits.items() if e.error]
        if errors:
            self.open_tab("notifications")
            QMessageBox.warning(self, "Hotkeys", "\n".join(errors))
            return
        s = self.settings
        s.models.local = self.local_panel.config()
        s.models.cloud = self.cloud_panel.config()
        s.models.escalation_confidence = self.escalation.value()
        s.models.max_cloud_requests_per_hour = self.max_cloud.value()
        s.models.send_screenshots_to_cloud = self.send_screens.isChecked()
        s.models.share_profile_with_cloud = self.share_profile.isChecked()
        s.notifications = self.notification_settings()
        for key, edit in self.hotkey_edits.items():
            setattr(s.hotkeys, key, edit.text())
        s.privacy.blocklist.programs = self.block_programs.programs()
        s.privacy.blocklist.rules = self.block_rules.rules()
        s.privacy.logging_enabled = self.logging.isChecked()
        s.privacy.retention_days = self.retention.value()
        s.gaze.enabled = self.gaze_enabled.isChecked()
        s.gaze.camera_index = self.camera.value()
        s.gaze.smoothing = self.smoothing.value()
        self.saved.emit()
        # Saved secrets now show as "Saved" and the new hotkeys belong to FollowEye.
        self.local_panel.refresh()
        self.cloud_panel.refresh()
        held = {e.text() for e in self.hotkey_edits.values()}
        for edit in self.hotkey_edits.values():
            edit.set_owned(held)
        self.save_status.setText("✓ Saved")
        self._status_timer.start()

    def pending_secrets(self) -> dict[str, str | None]:
        return {**self.local_panel.pending_secrets(), **self.cloud_panel.pending_secrets()}

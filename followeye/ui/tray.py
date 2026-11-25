from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ..config import Settings
from .icons import eye_icon


class Tray(QObject):
    mode_selected = Signal(str)
    pause_toggled = Signal()
    settings_requested = Signal(str)  # tab name
    calibrate_requested = Signal()
    gaze_dot_toggled = Signal()
    preview_requested = Signal()
    quit_requested = Signal()

    def __init__(self):
        super().__init__()
        self.icon = QSystemTrayIcon()
        self.menu = QMenu()
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self._activated)
        self.icon.show()

    def update(self, settings: Settings, paused: bool, status: str, gaze_dot: bool = False) -> None:
        mode = settings.active_mode()
        state = "paused" if paused else ("off" if not mode.observe else "active")
        self.icon.setIcon(eye_icon(state))
        tip = f"FollowEye – {mode.name}" + (" (paused)" if paused else "")
        self.icon.setToolTip(tip + (f"\n{status}" if status else ""))

        self.menu.clear()
        header = self.menu.addAction(tip)
        header.setEnabled(False)
        if status:
            note = self.menu.addAction(status)
            note.setEnabled(False)
        self.menu.addSeparator()
        self.menu.addSection("Mode")
        group = QActionGroup(self.menu)
        group.setExclusive(True)
        for m in settings.modes:
            action = QAction(m.name, self.menu, checkable=True, checked=m.id == mode.id)
            action.triggered.connect(lambda _checked=False, mode_id=m.id: self.mode_selected.emit(mode_id))
            group.addAction(action)
            self.menu.addAction(action)
        self.menu.addSeparator()
        pause = self.menu.addAction(f"Pause observation\t{settings.hotkeys.pause}")
        pause.setCheckable(True)
        pause.setChecked(paused)
        pause.triggered.connect(lambda _checked=False: self.pause_toggled.emit())
        self.menu.addSection("Eye tracking")
        self.menu.addAction("Calibrate eye tracking…").triggered.connect(lambda: self.calibrate_requested.emit())
        dot = self.menu.addAction("Show gaze dot")
        dot.setCheckable(True)
        dot.setChecked(gaze_dot)
        dot.triggered.connect(lambda _checked=False: self.gaze_dot_toggled.emit())
        self.menu.addAction("Camera preview…").triggered.connect(lambda: self.preview_requested.emit())
        self.menu.addSeparator()
        self.menu.addAction("Settings…").triggered.connect(lambda: self.settings_requested.emit("modes"))
        self.menu.addAction("History…").triggered.connect(lambda: self.settings_requested.emit("history"))
        self.menu.addSeparator()
        self.menu.addAction("Quit FollowEye").triggered.connect(lambda: self.quit_requested.emit())

    def message(self, title: str, text: str) -> None:
        self.icon.showMessage(title, text, eye_icon("active"), 6000)

    def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.settings_requested.emit("modes")

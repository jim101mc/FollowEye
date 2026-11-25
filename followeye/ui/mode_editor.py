from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..apps import AppCatalog
from ..modes import Mode, RuleAction
from .editors import BehaviourEditor, RulesEditor
from .program_picker import ProgramChecklist


class ModeEditorDialog(QDialog):
    """Create or edit a mode: which programs it watches (checklist) and how it reacts."""

    def __init__(self, mode: Mode, catalog: AppCatalog, name_taken: Callable[[str], bool], parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Mode: {mode.name}")
        self.resize(640, 640)
        self._mode = mode
        self._name_taken = name_taken

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(mode.name)
        form.addRow("Name", self.name)
        layout.addLayout(form)

        tabs = QTabWidget()
        layout.addWidget(tabs, 1)

        # Programs
        programs_tab = QWidget()
        programs_layout = QVBoxLayout(programs_tab)
        self.watch_all = QCheckBox("Watch all programs (except the blocklist)")
        self.watch_all.setChecked(mode.watch_all)
        self.foreground_only = QCheckBox("Only watch the window in the foreground")
        self.foreground_only.setChecked(mode.foreground_only)
        self.programs_hint = QLabel()
        self.programs_hint.setWordWrap(True)
        programs_layout.addWidget(self.watch_all)
        programs_layout.addWidget(self.foreground_only)
        programs_layout.addWidget(self.programs_hint)
        self.behaviour_editor = BehaviourEditor(mode.behaviour)
        self.checklist = ProgramChecklist(catalog, mode.programs, mode_behaviour=self.behaviour_editor.behaviour)
        programs_layout.addWidget(self.checklist, 1)
        tabs.addTab(programs_tab, "Programs")

        # Websites & titles
        self.rules = RulesEditor(mode.rules, with_action=True)
        rules_tab = QWidget()
        QVBoxLayout(rules_tab).addWidget(self.rules)
        tabs.addTab(rules_tab, "Websites && titles")

        # Behaviour
        behaviour_tab = QWidget()
        behaviour_layout = QVBoxLayout(behaviour_tab)
        behaviour_layout.addWidget(QLabel("Defaults for every program in this mode. "
                                          "Programs can override them on the Programs tab."))
        behaviour_layout.addWidget(self.behaviour_editor)
        tabs.addTab(behaviour_tab, "Behaviour")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.watch_all.toggled.connect(self._update_hint)
        self._update_hint()

    def _update_hint(self) -> None:
        if self.watch_all.isChecked():
            self.programs_hint.setText("Every program is watched. Tick a program only to give it custom behaviour.")
        else:
            self.programs_hint.setText("Only ticked programs, and websites/titles you add as “Watch” rules, are watched.")

    def _save(self) -> None:
        name = self.name.text().strip()
        if not name:
            QMessageBox.warning(self, "Mode", "Give the mode a name.")
            return
        if self._name_taken(name):
            QMessageBox.warning(self, "Mode", f"A mode called “{name}” already exists.")
            return
        rules = self.rules.rules()
        programs = self.checklist.programs()
        watches_something = self.watch_all.isChecked() or programs or any(
            r.action is RuleAction.INCLUDE for r in rules
        )
        if not watches_something:
            answer = QMessageBox.question(
                self, "Mode", "This mode doesn't watch any program or website. Save it anyway?"
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._mode = self._mode.model_copy(
            update={
                "name": name,
                "watch_all": self.watch_all.isChecked(),
                "foreground_only": self.foreground_only.isChecked(),
                "programs": programs,
                "rules": rules,
                "behaviour": self.behaviour_editor.behaviour(),
            }
        )
        self.accept()

    def mode(self) -> Mode:
        return self._mode

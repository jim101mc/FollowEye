"""Reusable editor widgets: hotkey recorder, behaviour checklist, rules table."""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from ..hotkeys import Hotkey, HotkeyError, parse_hotkey, validate_hotkey
from ..modes import (
    DEFAULT_THRESHOLDS,
    ROUTING_LABELS,
    TRIGGER_LABELS,
    Behaviour,
    Routing,
    RuleAction,
    RuleKind,
    Trigger,
    TriggerSetting,
    WindowRule,
)

HotkeyFreeCheck = Callable[[Hotkey], bool]


class HotkeyEdit(QWidget):
    """Records one key combination and explains why it can't be used, if it can't."""

    changed = Signal()

    def __init__(self, text: str, is_free: HotkeyFreeCheck, owned: set[str], parent=None):
        super().__init__(parent)
        self._is_free = is_free
        self._owned = set()  # combinations FollowEye itself currently holds
        for held in owned:
            try:
                self._owned.add(parse_hotkey(held).text)
            except HotkeyError:
                continue
        self._others: Callable[[], dict[str, str]] = dict
        self._error: str | None = None
        self.edit = QKeySequenceEdit(QKeySequence(text))
        self.edit.setMaximumSequenceLength(1)
        self.edit.setClearButtonEnabled(True)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.edit)
        layout.addWidget(self.status)
        self.edit.keySequenceChanged.connect(lambda _seq: self._validate())
        self._validate(emit=False)

    def set_owned(self, held: set[str]) -> None:
        """Combinations FollowEye itself now holds, so they don't count as taken by another program."""
        self._owned = set()
        for text in held:
            try:
                self._owned.add(parse_hotkey(text).text)
            except HotkeyError:
                continue
        self._validate(emit=False)

    def set_conflict_source(self, others: Callable[[], dict[str, str]]) -> None:
        """`others` returns {action label: hotkey text} of the other FollowEye hotkeys."""
        self._others = others
        self._validate(emit=False)

    def text(self) -> str:
        raw = self.edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
        try:
            return parse_hotkey(raw).text
        except HotkeyError:
            return raw

    @property
    def error(self) -> str | None:
        return self._error

    def _validate(self, emit: bool = True) -> None:
        self._error = self._check()
        if self._error:
            self.status.setText(self._error)
            self.status.setStyleSheet("color: #D9534F;")
        else:
            self.status.setText("")
        if emit:
            self.changed.emit()

    def _check(self) -> str | None:
        try:
            hotkey = validate_hotkey(self.text())
        except HotkeyError as e:
            return str(e)
        for label, other in self._others().items():
            try:
                if validate_hotkey(other).text == hotkey.text:
                    return f"Already used for “{label}”."
            except HotkeyError:
                continue
        if hotkey.text not in self._owned and not self._is_free(hotkey):
            return f"{hotkey.text} is already taken by another program."
        return None

    def revalidate(self) -> None:
        self._validate(emit=False)


class BehaviourEditor(QWidget):
    """Checklist of triggers (with thresholds) plus the local/cloud routing choice."""

    def __init__(self, behaviour: Behaviour, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        triggers = QGroupBox("When should FollowEye look at the screen?")
        grid = QGridLayout(triggers)
        self._checks: dict[Trigger, QCheckBox] = {}
        self._spins: dict[Trigger, QDoubleSpinBox] = {}
        for row, trigger in enumerate(Trigger):
            check = QCheckBox(TRIGGER_LABELS[trigger])
            grid.addWidget(check, row, 0)
            self._checks[trigger] = check
            if trigger in DEFAULT_THRESHOLDS:
                spin = QDoubleSpinBox(minimum=0.1, maximum=120.0, singleStep=0.5, decimals=1, suffix=" s")
                spin.setToolTip("How long before this counts")
                check.toggled.connect(spin.setEnabled)
                grid.addWidget(spin, row, 1)
                self._spins[trigger] = spin
        layout.addWidget(triggers)

        routing = QGroupBox("Which model may analyse it?")
        routing_layout = QVBoxLayout(routing)
        self._routing = QButtonGroup(self)
        self._routing_buttons: dict[Routing, QRadioButton] = {}
        for r in Routing:
            button = QRadioButton(ROUTING_LABELS[r])
            self._routing.addButton(button)
            routing_layout.addWidget(button)
            self._routing_buttons[r] = button
        layout.addWidget(routing)
        layout.addStretch(1)
        self.set_behaviour(behaviour)

    def set_behaviour(self, behaviour: Behaviour) -> None:
        for trigger, check in self._checks.items():
            check.setChecked(behaviour.enabled(trigger))
            if trigger in self._spins:
                self._spins[trigger].setValue(behaviour.threshold(trigger))
                self._spins[trigger].setEnabled(check.isChecked())
        self._routing_buttons[behaviour.routing].setChecked(True)

    def behaviour(self) -> Behaviour:
        triggers = {
            t: TriggerSetting(
                enabled=self._checks[t].isChecked(),
                threshold=self._spins[t].value() if t in self._spins else None,
            )
            for t in Trigger
        }
        routing = next(r for r, b in self._routing_buttons.items() if b.isChecked())
        return Behaviour(triggers=triggers, routing=routing)


class BehaviourDialog(QDialog):
    """Per-program override editor. Returns None from `result_behaviour()` to use the mode defaults."""

    def __init__(self, program_name: str, behaviour: Behaviour, has_override: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Behaviour for {program_name}")
        self._reset = False
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Custom behaviour for <b>{program_name}</b> in this mode."))
        self.editor = BehaviourEditor(behaviour)
        layout.addWidget(self.editor)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        reset = buttons.addButton("Use mode defaults", QDialogButtonBox.ButtonRole.ResetRole)
        reset.setEnabled(has_override)
        reset.clicked.connect(self._use_defaults)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _use_defaults(self) -> None:
        self._reset = True
        self.accept()

    def result_behaviour(self) -> Behaviour | None:
        return None if self._reset else self.editor.behaviour()


_KIND_LABELS = {RuleKind.DOMAIN: "Website domain", RuleKind.TITLE: "Window title"}
_ACTION_LABELS = {RuleAction.INCLUDE: "Watch", RuleAction.EXCLUDE: "Ignore"}


class RulesEditor(QWidget):
    """Table of website-domain and window-title rules."""

    HELP = (
        "Website domain rules match browser tabs: github.com also matches www.github.com and "
        "gist.github.com. Window title rules match text in the title bar; use * as a wildcard."
    )

    def __init__(self, rules: list[WindowRule], with_action: bool, parent=None):
        super().__init__(parent)
        self.with_action = with_action
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        help_label = QLabel(self.HELP)
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        columns = ["Type", "Pattern"] + (["Action"] if with_action else [])
        self.table = QTableWidget(0, len(columns))
        self.table.setHorizontalHeaderLabels(columns)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        add = QPushButton("Add rule")
        add.clicked.connect(lambda: self._add(WindowRule(kind=RuleKind.DOMAIN, pattern="")))
        remove = QPushButton("Remove selected")
        remove.clicked.connect(self._remove)
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch(1)
        layout.addLayout(row)
        for rule in rules:
            self._add(rule)

    def _add(self, rule: WindowRule) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        kind = QComboBox()
        for k, label in _KIND_LABELS.items():
            kind.addItem(label, k)
        kind.setCurrentIndex(list(_KIND_LABELS).index(rule.kind))
        self.table.setCellWidget(r, 0, kind)
        pattern = QLineEdit(rule.pattern)
        pattern.setPlaceholderText("e.g. github.com or *invoice*")
        self.table.setCellWidget(r, 1, pattern)
        if self.with_action:
            action = QComboBox()
            for a, label in _ACTION_LABELS.items():
                action.addItem(label, a)
            action.setCurrentIndex(list(_ACTION_LABELS).index(rule.action))
            self.table.setCellWidget(r, 2, action)
        if not rule.pattern:
            pattern.setFocus()

    def _remove(self) -> None:
        for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(r)

    def rules(self) -> list[WindowRule]:
        out = []
        for r in range(self.table.rowCount()):
            pattern = self.table.cellWidget(r, 1).text().strip()
            if not pattern:
                continue
            kind = self.table.cellWidget(r, 0).currentData()
            action = self.table.cellWidget(r, 2).currentData() if self.with_action else RuleAction.EXCLUDE
            out.append(WindowRule(kind=kind, pattern=pattern, action=action))
        return out

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from PySide6.QtCore import QFileInfo, QObject, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileIconProvider,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..apps import AppCatalog, AppInfo
from ..modes import Behaviour, ProgramEntry
from .editors import BehaviourDialog

FILTERS = ("All programs", "Installed", "Running now", "Ticked")
_installed_cache: list[AppInfo] | None = None


@dataclass
class _Row:
    exe: str
    name: str
    path: str = ""
    sources: set[str] = field(default_factory=set)
    override: Behaviour | None = None


class _Loader(QObject):
    loaded = Signal(list, list)  # installed, running

    def __init__(self, catalog: AppCatalog, refresh_installed: bool):
        super().__init__()
        self._catalog = catalog
        self._refresh_installed = refresh_installed

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        global _installed_cache
        if self._refresh_installed or _installed_cache is None:
            try:
                _installed_cache = self._catalog.installed()
            except Exception:  # scanning the Start Menu can fail on locked-down PCs
                _installed_cache = []
        try:
            running = self._catalog.running()
        except Exception:
            running = []
        self.loaded.emit(_installed_cache, running)


class ProgramChecklist(QWidget):
    """Searchable checklist of installed and running programs, with optional per-program overrides."""

    def __init__(
        self,
        catalog: AppCatalog,
        programs: list[ProgramEntry],
        mode_behaviour: Callable[[], Behaviour] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._catalog = catalog
        self._mode_behaviour = mode_behaviour  # None disables overrides (e.g. for the blocklist)
        self._rows: dict[str, _Row] = {p.exe: _Row(p.exe, p.name, override=p.override, sources={"saved"}) for p in programs}
        self._checked = {p.exe for p in programs}
        self._icons = QFileIconProvider()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        self.search = QLineEdit(placeholderText="Search programs…", clearButtonEnabled=True)
        self.filter = QComboBox()
        self.filter.addItems(FILTERS)
        self.refresh_button = QPushButton("Refresh")
        top.addWidget(self.search, 1)
        top.addWidget(self.filter)
        top.addWidget(self.refresh_button)
        layout.addLayout(top)

        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(self.list, 1)
        self.status = QLabel("Loading programs…")
        layout.addWidget(self.status)

        bottom = QHBoxLayout()
        self.override_button = QPushButton("Custom behaviour for this program…")
        self.override_button.setVisible(mode_behaviour is not None)
        self.override_button.setEnabled(False)
        bottom.addWidget(self.override_button)
        bottom.addStretch(1)
        layout.addLayout(bottom)

        self.search.textChanged.connect(self._apply_filter)
        self.filter.currentIndexChanged.connect(self._apply_filter)
        self.refresh_button.clicked.connect(lambda: self._load(refresh_installed=True))
        self.list.itemChanged.connect(self._item_changed)
        self.list.currentItemChanged.connect(self._update_override_button)
        self.list.itemDoubleClicked.connect(lambda _item: self._edit_override())
        self.override_button.clicked.connect(self._edit_override)
        self._populate()
        self._load(refresh_installed=False)

    def programs(self) -> list[ProgramEntry]:
        return [
            ProgramEntry(exe=r.exe, name=r.name, override=r.override if self._mode_behaviour else None)
            for r in sorted(self._rows.values(), key=lambda r: r.name.lower())
            if r.exe in self._checked
        ]

    # --- loading -------------------------------------------------------------

    def _load(self, refresh_installed: bool) -> None:
        self.refresh_button.setEnabled(False)
        self._loader = _Loader(self._catalog, refresh_installed)
        self._loader.loaded.connect(self._loaded)
        self._loader.start()

    def _loaded(self, installed: list[AppInfo], running: list[AppInfo]) -> None:
        for row in self._rows.values():
            row.sources.discard("running")
        for source, apps in (("installed", installed), ("running", running)):
            for app in apps:
                row = self._rows.get(app.exe)
                if row is None:
                    row = self._rows[app.exe] = _Row(app.exe, app.name, app.path)
                elif not row.path:
                    row.path = app.path
                    if row.name == row.exe:
                        row.name = app.name
                row.sources.add(source)
        self.refresh_button.setEnabled(True)
        self._populate()

    def _populate(self) -> None:
        current = self.list.currentItem().data(Qt.ItemDataRole.UserRole) if self.list.currentItem() else None
        self.list.blockSignals(True)
        self.list.clear()
        for row in sorted(self._rows.values(), key=lambda r: r.name.lower()):
            item = QListWidgetItem(self._label(row))
            item.setData(Qt.ItemDataRole.UserRole, row.exe)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if row.exe in self._checked else Qt.CheckState.Unchecked)
            if row.path:
                item.setIcon(self._icons.icon(QFileInfo(row.path)))
            if row.override is not None:
                font = QFont(item.font())
                font.setBold(True)
                item.setFont(font)
            self.list.addItem(item)
            if row.exe == current:
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)
        self._apply_filter()

    def _label(self, row: _Row) -> str:
        label = f"{row.name}   ({row.exe})"
        if "running" in row.sources:
            label += "  · running"
        if row.override is not None:
            label += "  · custom behaviour"
        return label

    # --- interaction -----------------------------------------------------------

    def _apply_filter(self) -> None:
        text = self.search.text().strip().lower()
        mode = self.filter.currentText()
        shown = 0
        for i in range(self.list.count()):
            item = self.list.item(i)
            row = self._rows[item.data(Qt.ItemDataRole.UserRole)]
            visible = (not text or text in row.name.lower() or text in row.exe) and (
                mode == "All programs"
                or (mode == "Installed" and "installed" in row.sources)
                or (mode == "Running now" and "running" in row.sources)
                or (mode == "Ticked" and row.exe in self._checked)
            )
            item.setHidden(not visible)
            shown += visible
        self.status.setText(f"{len(self._checked)} ticked · {shown} shown")

    def _item_changed(self, item: QListWidgetItem) -> None:
        exe = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            self._checked.add(exe)
        else:
            self._checked.discard(exe)
        self._update_override_button()
        self._apply_filter()

    def _update_override_button(self, *_args) -> None:
        item = self.list.currentItem()
        self.override_button.setEnabled(
            item is not None and item.data(Qt.ItemDataRole.UserRole) in self._checked
        )

    def _edit_override(self) -> None:
        item = self.list.currentItem()
        if self._mode_behaviour is None or item is None:
            return
        row = self._rows[item.data(Qt.ItemDataRole.UserRole)]
        if row.exe not in self._checked:
            return
        start = row.override or self._mode_behaviour().model_copy(deep=True)
        dialog = BehaviourDialog(row.name, start, row.override is not None, self)
        if dialog.exec():
            row.override = dialog.result_behaviour()
            self._populate()

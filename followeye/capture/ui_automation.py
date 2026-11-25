from __future__ import annotations

from dataclasses import dataclass, field

import uiautomation as auto

from ..skills.analysis import UiField

MAX_NODES = 600
MAX_DEPTH = 30
MAX_TEXT = 12000
_FIELD_TYPES = {
    auto.ControlType.EditControl: "text",
    auto.ControlType.ComboBoxControl: "dropdown",
}
_TEXT_TYPES = {
    auto.ControlType.TextControl,
    auto.ControlType.HyperlinkControl,
    auto.ControlType.HeaderControl,
    auto.ControlType.HeaderItemControl,
    auto.ControlType.ListItemControl,
    auto.ControlType.TreeItemControl,
    auto.ControlType.DataItemControl,
    auto.ControlType.ButtonControl,
    auto.ControlType.TabItemControl,
    auto.ControlType.MenuItemControl,
}


@dataclass
class UiSnapshot:
    text: str
    fields: list[UiField] = field(default_factory=list)


def _value(control) -> str:
    pattern = control.GetPattern(auto.PatternId.ValuePattern)
    return pattern.Value if pattern else ""


def snapshot(hwnd: int) -> UiSnapshot:
    """Visible text and form fields of a window from its UI Automation tree. Skips password fields."""
    root = auto.ControlFromHandle(hwnd)
    if root is None:
        return UiSnapshot("")
    focused = auto.GetFocusedControl()
    focused_id = focused.GetRuntimeId() if focused else None
    lines: list[str] = []
    fields: list[UiField] = []
    size = 0
    for index, (control, _depth) in enumerate(auto.WalkControl(root, maxDepth=MAX_DEPTH)):
        if index >= MAX_NODES or size >= MAX_TEXT:
            break
        if control.IsOffscreen:
            continue
        ctype = control.ControlType
        if ctype in _FIELD_TYPES:
            if control.IsPassword:
                continue
            ui_field = UiField(
                field_id=f"f{len(fields) + 1}",
                control_type=_FIELD_TYPES[ctype],
                label=control.Name or control.AutomationId or "",
                value=_value(control),
                focused=focused_id is not None and control.GetRuntimeId() == focused_id,
                handle=control,
            )
            fields.append(ui_field)
            continue
        if ctype == auto.ControlType.DocumentControl:
            pattern = control.GetPattern(auto.PatternId.TextPattern)
            text = pattern.DocumentRange.GetText(MAX_TEXT - size) if pattern else ""
        elif ctype in _TEXT_TYPES:
            text = control.Name
        else:
            continue
        text = (text or "").strip()
        if text:
            lines.append(text)
            size += len(text)
    return UiSnapshot("\n".join(lines)[:MAX_TEXT], fields)


def browser_url(hwnd: int) -> str | None:
    """Address-bar text of Chromium/Firefox browsers (the first edit control in the toolbar)."""
    root = auto.ControlFromHandle(hwnd)
    if root is None:
        return None
    edit = root.EditControl(searchDepth=12)
    if not edit.Exists(0, 0):
        return None
    return _value(edit) or None


def set_field_value(ui_field: UiField, value: str) -> bool:
    control = ui_field.handle
    if control is None:
        return False
    pattern = control.GetPattern(auto.PatternId.ValuePattern)
    if pattern is None or pattern.IsReadOnly:
        return False
    pattern.SetValue(value)
    return True

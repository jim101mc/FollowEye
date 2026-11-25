from __future__ import annotations

import os
from pathlib import Path

from ..apps import AppInfo
from . import windows

_SKIP_WORDS = ("uninstall", "readme", "help", "documentation", "release notes", "website")


class WindowsAppCatalog:
    def installed(self) -> list[AppInfo]:
        return installed_apps()

    def running(self) -> list[AppInfo]:
        return running_apps()


def _start_menu_dirs() -> list[Path]:
    dirs = []
    for env in ("APPDATA", "PROGRAMDATA"):
        base = os.environ.get(env)
        if base:
            dirs.append(Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    return [d for d in dirs if d.is_dir()]


def installed_apps() -> list[AppInfo]:
    """Programs with a Start Menu shortcut to an .exe."""
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    try:
        shell = win32com.client.Dispatch("WScript.Shell")
        found: dict[str, AppInfo] = {}
        for folder in _start_menu_dirs():
            for link in folder.rglob("*.lnk"):
                name = link.stem
                if any(word in name.lower() for word in _SKIP_WORDS):
                    continue
                try:
                    target = shell.CreateShortcut(str(link)).TargetPath
                except Exception:  # broken or protected shortcut
                    continue
                if target.lower().endswith(".exe") and os.path.exists(target):
                    exe = os.path.basename(target).lower()
                    found.setdefault(exe, AppInfo(exe, name, target))
        return sorted(found.values(), key=lambda a: a.name.lower())
    finally:
        pythoncom.CoUninitialize()


def running_apps() -> list[AppInfo]:
    """Programs that currently have a visible window."""
    found: dict[str, AppInfo] = {}
    for hwnd in windows.visible_top_level_windows():
        info = windows.basic_window_info(hwnd)
        if info is None:
            continue
        exe, path, title = info
        found.setdefault(exe, AppInfo(exe, _friendly_name(path) or title, path))
    return sorted(found.values(), key=lambda a: a.name.lower())


def _friendly_name(path: str) -> str | None:
    try:
        import win32api

        lang, codepage = win32api.GetFileVersionInfo(path, "\\VarFileInfo\\Translation")[0]
        return win32api.GetFileVersionInfo(path, f"\\StringFileInfo\\{lang:04x}{codepage:04x}\\FileDescription") or None
    except Exception:  # many executables carry no version resource
        return None

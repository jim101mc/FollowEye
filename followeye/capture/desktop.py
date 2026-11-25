from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from ..skills.analysis import UiField
from . import ui_automation

_UNSAFE = re.compile(r"[^\w\s.\-]", re.UNICODE)


def _clean_keyword(keyword: str) -> str:
    return _UNSAFE.sub(" ", keyword).strip()[:40]


def search_index(keywords: list[str], limit: int) -> list[str]:
    """Query the Windows Search index for files whose name contains every keyword."""
    import pythoncom
    import win32com.client

    terms = [k for k in (_clean_keyword(k) for k in keywords) if k]
    if not terms:
        return []
    conditions = " AND ".join(f"System.FileName LIKE '%{t}%'" for t in terms)
    query = (
        f"SELECT TOP {int(limit)} System.ItemPathDisplay FROM SYSTEMINDEX "
        f"WHERE scope='file:' AND System.Kind <> 'folder' AND {conditions} "
        "ORDER BY System.DateModified DESC"
    )
    pythoncom.CoInitialize()
    try:
        conn = win32com.client.Dispatch("ADODB.Connection")
        conn.Open("Provider=Search.CollatorDSO;Extended Properties='Application=Windows';")
        try:
            rs = win32com.client.Dispatch("ADODB.Recordset")
            rs.Open(query, conn)
            paths = []
            while not rs.EOF:
                paths.append(str(rs.Fields.Item("System.ItemPathDisplay").Value))
                rs.MoveNext()
            rs.Close()
            return paths
        finally:
            conn.Close()
    finally:
        pythoncom.CoUninitialize()


def search_recent(keywords: list[str], limit: int) -> list[str]:
    """Fallback: Windows' Recent Items shortcuts whose name contains every keyword."""
    import pythoncom
    import win32com.client

    recent = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Recent"
    terms = [k.lower() for k in (_clean_keyword(k) for k in keywords) if k]
    if not terms or not recent.is_dir():
        return []
    links = sorted(recent.glob("*.lnk"), key=lambda p: p.stat().st_mtime, reverse=True)
    pythoncom.CoInitialize()
    try:
        shell = win32com.client.Dispatch("WScript.Shell")
        out = []
        for link in links:
            if not all(t in link.stem.lower() for t in terms):
                continue
            target = shell.CreateShortcut(str(link)).TargetPath
            if target and os.path.isfile(target):
                out.append(target)
                if len(out) >= limit:
                    break
        return out
    finally:
        pythoncom.CoUninitialize()


class WindowsDesktop:
    """The skills' side effects. Called on a thread with UI Automation initialised."""

    def __init__(self, copy_text):
        self._copy_text = copy_text  # must marshal to the Qt UI thread

    def set_field(self, ui_field: UiField, value: str) -> bool:
        return ui_automation.set_field_value(ui_field, value)

    def open_path(self, path: str) -> None:
        os.startfile(path)

    def reveal_path(self, path: str) -> None:
        subprocess.Popen(["explorer.exe", "/select,", os.path.normpath(path)])

    def copy_text(self, text: str) -> None:
        self._copy_text(text)

    def search_files(self, keywords: list[str], limit: int) -> list[str]:
        try:
            found = search_index(keywords, limit)
        except Exception:  # Windows Search service disabled or unavailable
            found = []
        return found or search_recent(keywords, limit)

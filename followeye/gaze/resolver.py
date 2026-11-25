from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable

from ..modes import BROWSER_EXES, WindowInfo
from ..winapi import windows


class WindowResolver:
    """Screen point / foreground -> WindowInfo, looking up browser URLs only when a rule needs them."""

    def __init__(self, needs_url: Callable[[], bool], browser_url: Callable[[int], str | None]):
        self._needs_url = needs_url
        self._browser_url = browser_url
        self._urls: OrderedDict[tuple[int, str], str | None] = OrderedDict()

    def at_point(self, x: float, y: float) -> WindowInfo | None:
        hwnd = windows.window_from_point(x, y)
        return self.info(hwnd) if hwnd else None

    def foreground(self) -> WindowInfo | None:
        hwnd = windows.foreground_window()
        return self.info(hwnd) if hwnd else None

    def info(self, hwnd: int) -> WindowInfo | None:
        basic = windows.basic_window_info(hwnd)
        if basic is None:
            return None
        exe, _path, title = basic
        url = self._url(hwnd, title) if exe in BROWSER_EXES and self._needs_url() else None
        return WindowInfo(hwnd=hwnd, exe=exe, title=title, url=url, is_foreground=hwnd == windows.foreground_window())

    def _url(self, hwnd: int, title: str) -> str | None:
        key = (hwnd, title)  # the title changes when the tab or page changes
        if key not in self._urls:
            try:
                self._urls[key] = self._browser_url(hwnd)
            except Exception:  # UI Automation can fail on windows that are closing
                self._urls[key] = None
            while len(self._urls) > 128:
                self._urls.popitem(last=False)
        return self._urls[key]

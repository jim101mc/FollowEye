from __future__ import annotations

import ctypes
import queue
import threading
from collections.abc import Callable
from concurrent.futures import Future
from ctypes import wintypes

from ..hotkeys import MOD_NOREPEAT, Hotkey

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_HOTKEY = 0x0312
WM_APP = 0x8000
PM_NOREMOVE = 0x0000
_PROBE_ID = 0xBFFF

user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]


def is_hotkey_free(hotkey: Hotkey) -> bool:
    """True if no other program holds this combination. FollowEye's own bindings count as taken."""
    if user32.RegisterHotKey(None, _PROBE_ID, hotkey.modifiers | MOD_NOREPEAT, hotkey.vk):
        user32.UnregisterHotKey(None, _PROBE_ID)
        return True
    return False


class HotkeyManager:
    """Registers system-wide hotkeys on a dedicated thread with its own Win32 message loop."""

    def __init__(self) -> None:
        self._callbacks: dict[int, Callable[[], None]] = {}
        self._commands: queue.Queue[tuple[Callable[[], object], Future]] = queue.Queue()
        self._thread_id = 0
        self._ready = threading.Event()
        self._next_id = 1
        self._ids: dict[str, int] = {}
        self._thread = threading.Thread(target=self._run, name="hotkeys", daemon=True)
        self._thread.start()
        self._ready.wait()

    def bind(self, name: str, hotkey: Hotkey | None, callback: Callable[[], None]) -> bool:
        """(Re)bind `name`; returns False if Windows refused (combination already taken)."""
        return bool(self._call(lambda: self._bind(name, hotkey, callback)))

    def unbind(self, name: str) -> None:
        self._call(lambda: self._unbind(name))

    def stop(self) -> None:
        for name in list(self._ids):
            self.unbind(name)
        user32.PostThreadMessageW(self._thread_id, WM_APP + 1, 0, 0)

    def _call(self, fn: Callable[[], object]) -> object:
        future: Future = Future()
        self._commands.put((fn, future))
        user32.PostThreadMessageW(self._thread_id, WM_APP, 0, 0)
        return future.result(timeout=5)

    def _bind(self, name: str, hotkey: Hotkey | None, callback: Callable[[], None]) -> bool:
        self._unbind(name)
        if hotkey is None:
            return True
        hotkey_id = self._next_id
        self._next_id += 1
        if not user32.RegisterHotKey(None, hotkey_id, hotkey.modifiers | MOD_NOREPEAT, hotkey.vk):
            return False
        self._ids[name] = hotkey_id
        self._callbacks[hotkey_id] = callback
        return True

    def _unbind(self, name: str) -> None:
        hotkey_id = self._ids.pop(name, None)
        if hotkey_id is not None:
            user32.UnregisterHotKey(None, hotkey_id)
            self._callbacks.pop(hotkey_id, None)

    def _run(self) -> None:
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_NOREMOVE)  # creates the message queue
        self._thread_id = kernel32.GetCurrentThreadId()
        self._ready.set()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                callback = self._callbacks.get(msg.wParam)
                if callback is not None:
                    callback()
            elif msg.message == WM_APP:
                while not self._commands.empty():
                    fn, future = self._commands.get_nowait()
                    try:
                        future.set_result(fn())
                    except Exception as e:  # surface to the caller's thread
                        future.set_exception(e)
            elif msg.message == WM_APP + 1:
                break

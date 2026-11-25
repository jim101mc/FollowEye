"""Checks that need real Windows APIs; skipped elsewhere."""
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


def test_all_modules_import():
    import importlib

    for name in ("followeye.app", "followeye.capture.desktop", "followeye.winapi.glass"):
        importlib.import_module(name)


def test_hotkey_register_and_conflict():
    from followeye.hotkeys import validate_hotkey
    from followeye.winapi.hotkeys import HotkeyManager, is_hotkey_free

    hotkey = validate_hotkey("Ctrl+Alt+Shift+F11")
    manager = HotkeyManager()
    try:
        assert manager.bind("test", hotkey, lambda: None)
        assert not is_hotkey_free(hotkey)  # now held by us
        manager.unbind("test")
        assert is_hotkey_free(hotkey)
    finally:
        manager.stop()


def test_windows_ocr_reads_text():
    import cv2
    import numpy as np

    from followeye.capture.ocr import WindowsOcr

    ocr = WindowsOcr()
    if not ocr.available:
        pytest.skip("No OCR language installed")
    image = np.full((200, 900, 4), 255, np.uint8)
    cv2.putText(image, "Invoice number 4711", (20, 120), cv2.FONT_HERSHEY_SIMPLEX, 2.0, (0, 0, 0, 255), 4)
    assert "4711" in ocr.recognize(image)


def test_gaze_dot_does_not_hide_the_window_under_it(qapp):
    from followeye.ui.gaze_dot import GazeDot
    from followeye.winapi import windows

    dot = GazeDot()
    dpr = qapp.primaryScreen().devicePixelRatio()
    dot.update_gaze(200 * dpr, 200 * dpr)
    qapp.processEvents()
    try:
        assert windows.window_from_point(200 * dpr, 200 * dpr) != int(dot.winId())
    finally:
        dot.hide()


def test_foreground_and_rect_lookups_do_not_crash():
    from followeye.winapi import windows

    hwnd = windows.foreground_window()
    if hwnd:
        windows.window_info(hwnd)
        windows.window_rect(hwnd)
    windows.visible_top_level_windows()

import pytest

from followeye.hotkeys import MOD_CONTROL, MOD_SHIFT, MOD_WIN, HotkeyError, parse_hotkey, validate_hotkey


def test_parse_qt_portable_text():
    hk = parse_hotkey("Ctrl+Shift+F")
    assert hk.modifiers == MOD_CONTROL | MOD_SHIFT
    assert hk.vk == ord("F")
    assert hk.text == "Ctrl+Shift+F"


def test_canonical_order_and_aliases():
    assert parse_hotkey("shift+meta+return").text == "Shift+Win+Enter"
    assert parse_hotkey("Meta+F12").modifiers == MOD_WIN
    assert parse_hotkey("Ctrl+F12").vk == 0x7B


def test_rejects_plain_or_shift_only():
    with pytest.raises(HotkeyError):
        validate_hotkey("Shift+A")
    with pytest.raises(HotkeyError):
        validate_hotkey("A")


def test_rejects_reserved():
    with pytest.raises(HotkeyError, match="reserved"):
        validate_hotkey("Win+L")
    with pytest.raises(HotkeyError):
        validate_hotkey("Alt+F4")


def test_rejects_two_keys_and_unknown_keys():
    with pytest.raises(HotkeyError):
        parse_hotkey("Ctrl+A+B")
    with pytest.raises(HotkeyError):
        parse_hotkey("Ctrl+NoSuchKey")
    with pytest.raises(HotkeyError):
        parse_hotkey("Ctrl+Shift")


def test_defaults_are_valid():
    from followeye.config import HotkeySettings

    for text in HotkeySettings().model_dump().values():
        validate_hotkey(text)

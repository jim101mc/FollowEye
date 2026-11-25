from __future__ import annotations

from dataclasses import dataclass

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

_MODIFIER_NAMES = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "meta": MOD_WIN,  # Qt calls the Windows key "Meta"
}

_MODIFIER_ORDER = [(MOD_CONTROL, "Ctrl"), (MOD_ALT, "Alt"), (MOD_SHIFT, "Shift"), (MOD_WIN, "Win")]

_NAMED_KEYS: dict[str, tuple[int, str]] = {
    "enter": (0x0D, "Enter"),
    "return": (0x0D, "Enter"),
    "backspace": (0x08, "Backspace"),
    "tab": (0x09, "Tab"),
    "space": (0x20, "Space"),
    "esc": (0x1B, "Esc"),
    "escape": (0x1B, "Esc"),
    "pause": (0x13, "Pause"),
    "ins": (0x2D, "Insert"),
    "insert": (0x2D, "Insert"),
    "del": (0x2E, "Delete"),
    "delete": (0x2E, "Delete"),
    "home": (0x24, "Home"),
    "end": (0x23, "End"),
    "pgup": (0x21, "PgUp"),
    "pageup": (0x21, "PgUp"),
    "pgdown": (0x22, "PgDown"),
    "pagedown": (0x22, "PgDown"),
    "left": (0x25, "Left"),
    "up": (0x26, "Up"),
    "right": (0x27, "Right"),
    "down": (0x28, "Down"),
    ";": (0xBA, ";"),
    "=": (0xBB, "="),
    ",": (0xBC, ","),
    "-": (0xBD, "-"),
    ".": (0xBE, "."),
    "/": (0xBF, "/"),
    "`": (0xC0, "`"),
    "[": (0xDB, "["),
    "\\": (0xDC, "\\"),
    "]": (0xDD, "]"),
    "'": (0xDE, "'"),
}
for _n in range(1, 25):
    _NAMED_KEYS[f"f{_n}"] = (0x6F + _n, f"F{_n}")

# Combinations Windows reserves or that would break normal use of the PC.
_RESERVED = {
    "Ctrl+Alt+Delete",
    "Ctrl+Shift+Esc",
    "Ctrl+Esc",
    "Alt+Tab",
    "Alt+Esc",
    "Alt+F4",
    "Alt+Space",
    "Ctrl+Alt+Tab",
    "Win+L",
    "Win+D",
    "Win+E",
    "Win+R",
    "Win+Tab",
    "Win+X",
    "Win+I",
    "Win+A",
    "Win+S",
    "Win+V",
}


class HotkeyError(ValueError):
    pass


@dataclass(frozen=True)
class Hotkey:
    modifiers: int
    vk: int
    text: str


def parse_hotkey(text: str) -> Hotkey:
    """Parse "Ctrl+Shift+F" style text (as written by Qt's QKeySequence) into a Win32 hotkey."""
    raw = text.strip()
    if not raw:
        raise HotkeyError("No key combination set.")
    # Split on "+", but keep a trailing "+" key ("Ctrl++") intact.
    parts = [p for p in raw.split("+") if p]
    if raw.endswith("++"):
        parts.append("+")
    modifiers = 0
    key: tuple[int, str] | None = None
    for part in parts:
        name = part.strip().lower()
        if name in _MODIFIER_NAMES:
            modifiers |= _MODIFIER_NAMES[name]
            continue
        if key is not None:
            raise HotkeyError("Use exactly one non-modifier key.")
        key = _key_for(name)
    if key is None:
        raise HotkeyError("Add a key after the modifiers, e.g. Ctrl+Shift+F.")
    canonical = "+".join([label for flag, label in _MODIFIER_ORDER if modifiers & flag] + [key[1]])
    return Hotkey(modifiers=modifiers, vk=key[0], text=canonical)


def _key_for(name: str) -> tuple[int, str]:
    if len(name) == 1 and ("a" <= name <= "z" or "0" <= name <= "9"):
        return ord(name.upper()), name.upper()
    if name in _NAMED_KEYS:
        return _NAMED_KEYS[name]
    raise HotkeyError(f"Key '{name}' is not supported.")


def validate_hotkey(text: str) -> Hotkey:
    """Parse and reject combinations that are unusable as a global hotkey."""
    hotkey = parse_hotkey(text)
    if not hotkey.modifiers & (MOD_CONTROL | MOD_ALT | MOD_WIN):
        raise HotkeyError("Include Ctrl, Alt or Win so normal typing isn't captured.")
    if hotkey.text in _RESERVED:
        raise HotkeyError(f"{hotkey.text} is reserved by Windows.")
    return hotkey

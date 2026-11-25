from __future__ import annotations

from collections.abc import Callable

from pynput import keyboard, mouse


class ActivityListener:
    """Global keyboard and scroll hooks. Only timestamps are reported: no key is ever recorded."""

    def __init__(self, on_key: Callable[[], None], on_scroll: Callable[[int, int], None]):
        self._keyboard = keyboard.Listener(on_press=lambda _key: on_key())
        self._mouse = mouse.Listener(on_scroll=lambda x, y, _dx, _dy: on_scroll(x, y))

    def start(self) -> None:
        self._keyboard.start()
        self._mouse.start()

    def stop(self) -> None:
        self._keyboard.stop()
        self._mouse.stop()

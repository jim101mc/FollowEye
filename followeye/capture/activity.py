from __future__ import annotations

from collections.abc import Callable

from pynput import keyboard, mouse


class ActivityListener:
    """Global keyboard and mouse hooks. Keys are never recorded: only that a key was pressed."""

    def __init__(
        self,
        on_key: Callable[[], None],
        on_scroll: Callable[[int, int], None],
        on_click: Callable[[int, int], None],
    ):
        self._keyboard = keyboard.Listener(on_press=lambda _key: on_key())
        self._mouse = mouse.Listener(
            on_scroll=lambda x, y, _dx, _dy: on_scroll(x, y),
            on_click=lambda x, y, button, pressed: on_click(x, y) if pressed and button == mouse.Button.left else None,
        )

    def start(self) -> None:
        self._keyboard.start()
        self._mouse.start()

    def stop(self) -> None:
        self._keyboard.stop()
        self._mouse.stop()

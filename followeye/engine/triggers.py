from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..modes import Behaviour, Trigger, WindowInfo

AWAY_SECONDS = 2.0
SCROLL_SETTLE_SECONDS = 0.4


@dataclass(frozen=True)
class TriggerEvent:
    trigger: Trigger
    window: WindowInfo
    behaviour: Behaviour
    at: float
    clipboard: str | None = None


BehaviourLookup = Callable[[WindowInfo], Behaviour | None]


class TriggerEngine:
    """Turns raw gaze/keyboard/scroll/clipboard signals into analysis triggers.

    Not thread-safe: feed it from a single thread. Times are monotonic seconds.
    """

    def __init__(self, behaviour_for: BehaviourLookup):
        self._behaviour_for = behaviour_for
        self.reset()

    def reset(self) -> None:
        self.current: WindowInfo | None = None
        self._current_since = 0.0
        self._last_seen = 0.0
        self._candidate: WindowInfo | None = None
        self._candidate_since = 0.0
        self._typing_window: WindowInfo | None = None
        self._last_key = 0.0
        self._scroll_window: WindowInfo | None = None
        self._last_scroll = 0.0
        self._last_activity = 0.0
        self._dwell_fired = False

    def on_gaze(self, t: float, window: WindowInfo | None) -> list[TriggerEvent]:
        if window is None:
            if self.current is not None and t - self._last_seen > AWAY_SECONDS:
                self.current = None
                self._candidate = None
            return []
        self._last_seen = t
        if self.current is not None and window.hwnd == self.current.hwnd:
            self._candidate = None
            if window.title != self.current.title:
                # Same window, new content (e.g. browser tab): treat as a switch.
                self.current = window
                return self._settle(t, window)
            return []
        if self._candidate is None or self._candidate.hwnd != window.hwnd:
            self._candidate = window
            self._candidate_since = t
            return []
        behaviour = self._behaviour_for(window)
        threshold = behaviour.threshold(Trigger.WINDOW_SWITCH) if behaviour else 0.5
        if t - self._candidate_since < threshold:
            return []
        self._candidate = None
        self.current = window
        return self._settle(t, window, behaviour)

    def _settle(self, t: float, window: WindowInfo, behaviour: Behaviour | None = None) -> list[TriggerEvent]:
        self._current_since = t
        self._mark_activity(t)
        behaviour = behaviour or self._behaviour_for(window)
        return self._emit(Trigger.WINDOW_SWITCH, window, behaviour, t)

    def on_key(self, t: float, foreground: WindowInfo | None) -> list[TriggerEvent]:
        if foreground is None:
            return []
        behaviour = self._behaviour_for(foreground)
        if behaviour is None:
            return []
        self._mark_activity(t)
        started = self._typing_window is None or self._typing_window.hwnd != foreground.hwnd
        self._typing_window = foreground
        self._last_key = t
        return self._emit(Trigger.TYPING_START, foreground, behaviour, t) if started else []

    def on_scroll(self, t: float, window: WindowInfo | None) -> list[TriggerEvent]:
        if window is None or self._behaviour_for(window) is None:
            return []
        self._mark_activity(t)
        self._scroll_window = window
        self._last_scroll = t
        return []

    def on_clipboard(self, t: float, window: WindowInfo | None, text: str) -> list[TriggerEvent]:
        if window is None:
            return []
        behaviour = self._behaviour_for(window)
        return self._emit(Trigger.CLIPBOARD, window, behaviour, t, clipboard=text)

    def tick(self, t: float) -> list[TriggerEvent]:
        events: list[TriggerEvent] = []
        if self._typing_window is not None:
            behaviour = self._behaviour_for(self._typing_window)
            stop_after = behaviour.threshold(Trigger.TYPING_STOP) if behaviour else 2.0
            if t - self._last_key >= stop_after:
                window, self._typing_window = self._typing_window, None
                events += self._emit(Trigger.TYPING_STOP, window, behaviour, t)
        if self._scroll_window is not None and t - self._last_scroll >= SCROLL_SETTLE_SECONDS:
            window, self._scroll_window = self._scroll_window, None
            events += self._emit(Trigger.SCROLL, window, self._behaviour_for(window), t)
        if self.current is not None and not self._dwell_fired and self._typing_window is None:
            behaviour = self._behaviour_for(self.current)
            if behaviour is not None:
                idle_since = max(self._last_activity, self._current_since)
                if t - idle_since >= behaviour.threshold(Trigger.IDLE_DWELL):
                    self._dwell_fired = True
                    events += self._emit(Trigger.IDLE_DWELL, self.current, behaviour, t)
        return events

    def _mark_activity(self, t: float) -> None:
        self._last_activity = t
        self._dwell_fired = False

    @staticmethod
    def _emit(
        trigger: Trigger,
        window: WindowInfo,
        behaviour: Behaviour | None,
        t: float,
        clipboard: str | None = None,
    ) -> list[TriggerEvent]:
        if behaviour is None or not behaviour.enabled(trigger):
            return []
        return [TriggerEvent(trigger, window, behaviour, t, clipboard)]

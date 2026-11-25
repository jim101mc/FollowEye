from followeye.engine.triggers import TriggerEngine
from followeye.modes import Behaviour, Trigger, TriggerSetting, WindowInfo

A = WindowInfo(hwnd=1, exe="code.exe", title="a")
B = WindowInfo(hwnd=2, exe="chrome.exe", title="b")
BLOCKED = WindowInfo(hwnd=3, exe="keepass.exe", title="vault")


def make(behaviour=None):
    behaviour = behaviour or Behaviour()
    return TriggerEngine(lambda w: None if w.exe == "keepass.exe" else behaviour)


def kinds(events):
    return [e.trigger for e in events]


def settle(engine, window, t0=0.0):
    engine.on_gaze(t0, window)
    return engine.on_gaze(t0 + 0.6, window)


def test_window_switch_needs_dwell():
    e = make()
    assert e.on_gaze(0.0, A) == []
    assert e.on_gaze(0.3, A) == []
    assert kinds(e.on_gaze(0.6, A)) == [Trigger.WINDOW_SWITCH]
    assert e.on_gaze(0.7, A) == []  # no repeat while staying


def test_quick_glance_is_ignored():
    e = make()
    settle(e, A)
    e.on_gaze(1.0, B)
    assert e.on_gaze(1.2, A) == []
    assert e.current.hwnd == A.hwnd


def test_title_change_in_same_window_counts_as_switch():
    e = make()
    settle(e, A)
    retitled = WindowInfo(hwnd=1, exe="code.exe", title="other tab")
    assert kinds(e.on_gaze(1.0, retitled)) == [Trigger.WINDOW_SWITCH]


def test_typing_start_and_stop():
    e = make()
    assert kinds(e.on_key(0.0, A)) == [Trigger.TYPING_START]
    assert e.on_key(0.5, A) == []
    assert e.tick(2.0) == []
    assert kinds(e.tick(2.6)) == [Trigger.TYPING_STOP]


def test_scroll_fires_once_after_settling():
    e = make()
    e.on_scroll(0.0, A)
    e.on_scroll(0.1, A)
    assert e.tick(0.3) == []
    assert kinds(e.tick(0.6)) == [Trigger.SCROLL]
    assert e.tick(1.0) == []


def test_idle_dwell_fires_once_and_resets_on_activity():
    e = make()
    settle(e, A)
    assert e.tick(3.0) == []
    assert kinds(e.tick(5.7)) == [Trigger.IDLE_DWELL]
    assert e.tick(20.0) == []
    e.on_scroll(21.0, A)
    e.tick(21.5)  # scroll settles
    assert kinds(e.tick(26.1)) == [Trigger.IDLE_DWELL]


def test_disabled_trigger_and_custom_threshold():
    b = Behaviour()
    b.triggers[Trigger.TYPING_START] = TriggerSetting(enabled=False)
    b.triggers[Trigger.TYPING_STOP] = TriggerSetting(threshold=5.0)
    e = make(b)
    assert e.on_key(0.0, A) == []
    assert e.tick(3.0) == []
    assert kinds(e.tick(5.1)) == [Trigger.TYPING_STOP]


def test_blocked_window_never_triggers():
    e = make()
    assert settle(e, BLOCKED) == []
    assert e.on_key(1.0, BLOCKED) == []
    assert e.on_clipboard(1.0, BLOCKED, "secret") == []
    assert e.tick(30.0) == []


def test_clipboard_carries_text():
    e = make()
    [event] = e.on_clipboard(0.0, A, "hello")
    assert event.clipboard == "hello"


def test_looking_away_clears_current_window():
    e = make()
    settle(e, A)
    e.on_gaze(1.0, None)
    e.on_gaze(3.0, None)
    assert e.current is None

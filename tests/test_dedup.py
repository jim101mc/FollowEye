import numpy as np

from followeye.engine.dedup import Deduplicator, thumbnail
from followeye.modes import Trigger


def image(value=0, h=720, w=1280):
    return np.full((h, w, 4), value, dtype=np.uint8)


def test_thumbnail_shape_and_small_images():
    assert thumbnail(image()).shape == (36, 64)
    assert thumbnail(image(h=20, w=30)).shape == (20, 30)


def test_unchanged_window_is_skipped_until_cooldown():
    d = Deduplicator(cooldown=30)
    thumb = thumbnail(image(10))
    assert d.should_capture(1, thumb, 0.0, Trigger.SCROLL)
    d.mark(1, thumb, "h", 0.0)
    assert not d.should_capture(1, thumb, 5.0, Trigger.SCROLL)
    assert d.should_capture(1, thumb, 31.0, Trigger.SCROLL)


def test_pixel_change_threshold():
    d = Deduplicator(pixel_change=0.05)
    base = image(10)
    d.mark(1, thumbnail(base), "h", 0.0)
    small = base.copy()
    small[:20, :20] = 255  # tiny change
    assert not d.should_capture(1, thumbnail(small), 1.0, Trigger.SCROLL)
    big = base.copy()
    big[:200, :] = 255
    assert d.should_capture(1, thumbnail(big), 1.0, Trigger.SCROLL)


def test_clipboard_always_captures_and_text_hash_dedups():
    d = Deduplicator()
    thumb = thumbnail(image())
    d.mark(1, thumb, "same", 0.0)
    assert d.should_capture(1, thumb, 1.0, Trigger.CLIPBOARD)
    assert not d.should_analyze(1, "same", 1.0)
    assert d.should_analyze(1, "different", 1.0)


def test_windows_tracked_separately_and_bounded():
    d = Deduplicator(max_windows=2)
    thumb = thumbnail(image())
    for key in (1, 2, 3):
        d.mark(key, thumb, "h", 0.0)
    assert d.should_capture(1, thumb, 1.0, Trigger.SCROLL)  # evicted
    assert not d.should_capture(3, thumb, 1.0, Trigger.SCROLL)

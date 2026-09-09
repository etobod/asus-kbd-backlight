import time

from asus_kbd_backlight import config
from asus_kbd_backlight.app import Controller
from asus_kbd_backlight.backlight import LEVEL_OFF


def _make(timeout=0.1, on_level=1):
    cfg = config.Config(timeout=timeout, on_level=on_level).validated()
    return Controller(cfg, dry_run=True)


def test_startup_forces_off():
    c = _make()
    c._apply(on=False)
    assert c._backlight.last_level == LEVEL_OFF
    assert c._is_on is False


def test_apply_only_on_transition():
    c = _make(on_level=2)
    c._apply(on=False)

    c._backlight.last_level = None  # sentinel: no call happened
    c._apply(on=True)
    assert c._backlight.last_level == 2  # on -> DEVS(.., on_level)

    c._backlight.last_level = None
    c._apply(on=True)  # same state again
    assert c._backlight.last_level is None  # no second call


def test_tick_tracks_idle_timeout():
    c = _make(timeout=0.05)
    c._apply(on=False)

    c._hook.last_key = time.monotonic()
    c.tick()
    assert c._is_on is True  # just typed -> on

    time.sleep(0.08)
    c.tick()
    assert c._is_on is False  # idle past timeout -> off

    c._hook.last_key = time.monotonic()
    c.tick()
    assert c._is_on is True  # typing again -> back on

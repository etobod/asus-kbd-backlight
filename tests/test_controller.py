import threading
import time

from asus_kbd_backlight import config
from asus_kbd_backlight.app import FAIL_RETRY_INTERVAL, Controller
from asus_kbd_backlight.backlight import LEVEL_OFF, BacklightError


def _make(timeout=0.1, on_level=1):
    cfg = config.Config(timeout=timeout, on_level=on_level).validated()
    return Controller(cfg, dry_run=True)


class _FlakyBacklight:
    """set_level fails `fail_times` times, then succeeds. Counts calls."""

    def __init__(self, fail_times=0, exc=None):
        self.fail_times = fail_times
        self.exc = exc or BacklightError("boom")
        self.calls = 0
        self.last_level = None

    def set_level(self, level):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exc
        self.last_level = level


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


def test_apply_swallows_non_backlighterror():
    # NFR-4: no backlight fault may propagate out of _apply / kill the worker.
    c = _make()
    c._backlight = _FlakyBacklight(fail_times=1, exc=RuntimeError("unexpected com_error"))
    c._apply(on=True)  # must not raise
    assert c._is_on is None  # state not advanced on failure


def test_apply_retry_dedup_and_recovery(caplog):
    c = _make()
    c._backlight = _FlakyBacklight(fail_times=2)

    with caplog.at_level("INFO"):
        c._apply(on=True)                     # fail 1 -> logs error
        c._last_attempt = 0.0                 # defeat the backoff for the test
        c._apply(on=True)                     # fail 2 -> same msg, no re-log
        c._last_attempt = 0.0
        c._apply(on=True)                     # success -> recovered + on

    assert c._is_on is True
    assert c._backlight.last_level == c._cfg.on_level
    errors = [r for r in caplog.records if r.levelname == "ERROR" and "boom" in r.message]
    assert len(errors) == 1                   # repeating failure logged once
    assert any("recovered" in r.message for r in caplog.records)


def test_apply_backs_off_on_persistent_failure():
    c = _make()
    c._backlight = _FlakyBacklight(fail_times=999)

    for _ in range(20):                       # 20 ticks in a tight loop
        c._apply(on=True)
    assert c._backlight.calls == 1            # only the first got through; rest backed off

    c._last_attempt -= FAIL_RETRY_INTERVAL + 1  # pretend the interval elapsed
    c._apply(on=True)
    assert c._backlight.calls == 2            # now it retries


def test_run_turns_backlight_off_on_exit():
    # FR-6: the worker's trailing _apply(on=False) restores the off state.
    c = _make(timeout=5.0)
    c._hook.last_key = time.monotonic()       # "just typed" -> worker turns it on
    t = threading.Thread(target=c._run, daemon=True)
    t.start()
    time.sleep(0.05)
    assert c._is_on is True
    c._stop.set()
    t.join(timeout=2.0)
    assert not t.is_alive()
    assert c._backlight.last_level == LEVEL_OFF


def test_stop_warns_when_worker_hangs(caplog):
    # NFR-1/FR-6 gap: a stuck worker must be reported, hook still removed.
    c = _make()
    hang = threading.Event()
    c._worker = threading.Thread(target=hang.wait, daemon=True)
    c._worker.start()
    try:
        with caplog.at_level("WARNING"):
            c.stop(join_timeout=0.05)
        assert any("did not stop" in r.message for r in caplog.records)
    finally:
        hang.set()
        c._worker.join(timeout=1.0)

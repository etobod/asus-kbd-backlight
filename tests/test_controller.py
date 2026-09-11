import threading
import time

from asus_kbd_backlight import config
from asus_kbd_backlight.app import CONFIG_POLL_TICKS, FAIL_RETRY_INTERVAL, Controller
from asus_kbd_backlight.backlight import LEVEL_OFF, BacklightError


def _make(timeout=0.1, on_level=1, config_path=None):
    cfg = config.Config(timeout=timeout, on_level=on_level).validated()
    return Controller(cfg, dry_run=True, config_path=config_path)


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


def test_continuous_typing_keeps_it_steady_no_flicker():
    # FR-3: while the user keeps typing, the backlight is driven ON exactly once
    # and never toggled - _apply is a no-op once the state matches.
    c = _make(timeout=0.2)
    c._backlight = _FlakyBacklight(fail_times=0)  # counts set_level() calls
    c._apply(on=False)                            # baseline off (call #1)

    start = time.monotonic()
    while time.monotonic() - start < 0.5:         # 0.5 s of "typing", past timeout
        c._hook.last_key = time.monotonic()       # a fresh keystroke every tick
        c.tick()
        time.sleep(0.01)

    assert c._is_on is True
    assert c._backlight.last_level == c._cfg.on_level
    assert c._backlight.calls == 2                # off baseline + one ON, no churn


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


def test_pause_forces_off_and_ignores_keystrokes():
    # FR-8: Pause stops idle tracking; the worker's next tick turns the light
    # off (pause() itself never issues a backlight call - that must stay on the
    # worker thread). It then stays off however much the user types.
    c = _make(timeout=5.0)
    c._hook.last_key = time.monotonic()
    c.tick()
    assert c._is_on is True

    c.pause()
    assert c.paused is True
    assert c._is_on is True  # pause() only set the flag

    c.tick()  # the worker tick is what turns it off
    assert c._is_on is False
    assert c._backlight.last_level == LEVEL_OFF

    c._hook.last_key = time.monotonic()  # keep "typing"
    c.tick()
    assert c._is_on is False  # still held off while paused

    c.resume()
    assert c.paused is False
    c._hook.last_key = time.monotonic()
    c.tick()
    assert c._is_on is True  # tracking again


def test_toggle_pause_reports_the_new_state():
    c = _make()
    assert c.toggle_pause() is True
    assert c._paused is True
    assert c.toggle_pause() is False
    assert c._paused is False


def test_paused_apply_refuses_a_raced_on():
    # FR-8 race: a worker tick that read _paused as False, was preempted while
    # pause() ran, and then resumes with on=True must not relight the keyboard.
    c = _make(timeout=5.0)
    c.pause()

    c._apply(on=True)  # the stale tick landing after pause()
    assert c._is_on is not True  # the guard refused to turn it on
    assert c._backlight.last_level is None  # no set_level() call at all

    c.tick()  # the next paused tick drives it off, from the worker thread
    assert c._is_on is False
    assert c._backlight.last_level == LEVEL_OFF


def test_paused_tick_keeps_retrying_off_after_a_failed_pause():
    # FR-8 + NFR-4: if a WMI failure back-off is in progress, the first paused
    # tick's _apply(on=False) bails; a later paused tick must still drive the
    # light off once the back-off elapses, not give up for the whole pause.
    c = _make(timeout=5.0)
    c._is_on = True
    c._last_error = "boom"
    c._last_attempt = time.monotonic()  # back-off active
    c.pause()
    c.tick()
    assert c._is_on is True  # the off attempt was suppressed by the back-off

    c._last_attempt -= FAIL_RETRY_INTERVAL + 1  # interval elapsed
    c.tick()  # paused tick retries the off, backend now fine
    assert c._is_on is False
    assert c._backlight.last_level == LEVEL_OFF


def test_live_reload_swaps_config_and_issues_no_backlight_call(tmp_path):
    # FR-11: a hand-edit to config.toml is picked up while running; a reload is
    # never a control call (NFR-2) - only a later tick acts on the new value.
    path = tmp_path / "config.toml"
    config.save(config.Config(timeout=3.0, on_level=1), path)
    c = _make(timeout=3.0, config_path=path)

    config.save(config.Config(timeout=42.0, on_level=3), path)
    c._reload_config_if_changed()

    assert c.config.timeout == 42.0
    assert c.config.on_level == 3
    assert c._backlight.last_level is None  # no set_level() during the swap


def test_live_reload_parses_the_bytes_it_read_not_a_reopened_path(tmp_path):
    # TOCTOU: a delete-and-recreate editor can remove the file between "did it
    # change?" and the parse. The reload must parse the bytes it already read,
    # not re-open the path and silently get config.load()'s defaults.
    path = tmp_path / "config.toml"
    config.save(config.Config(timeout=5.0), path)
    c = _make(timeout=5.0, config_path=path)

    config.save(config.Config(timeout=30.0), path)
    real_read = c._read_config

    def _read_then_vanish():
        data = real_read()
        path.unlink()  # gone before the parse
        return data

    c._read_config = _read_then_vanish
    c._reload_config_if_changed()
    assert c.config.timeout == 30.0  # from the bytes, not reset to the 3.0 default


def test_live_reload_keeps_old_config_and_logs_once_on_malformed(tmp_path, caplog):
    path = tmp_path / "config.toml"
    config.save(config.Config(timeout=5.0), path)
    c = _make(timeout=5.0, config_path=path)

    path.write_text("timeout = -9\n")  # invalid: rejected by validated()
    with caplog.at_level("WARNING"):
        c._reload_config_if_changed()
        path.write_text("timeout = -9 # still bad\n")
        c._reload_config_if_changed()

    assert c.config.timeout == 5.0  # unchanged
    warnings = [r for r in caplog.records if "config reload failed" in r.message]
    assert len(warnings) == 1  # logged once, not every poll


def test_live_reload_recovers_after_a_bad_edit(tmp_path):
    path = tmp_path / "config.toml"
    config.save(config.Config(timeout=5.0), path)
    c = _make(timeout=5.0, config_path=path)

    path.write_text("timeout = oops\n")
    c._reload_config_if_changed()
    assert c.config.timeout == 5.0

    config.save(config.Config(timeout=8.0), path)
    c._reload_config_if_changed()
    assert c.config.timeout == 8.0


def test_live_reload_retargets_the_backlight_device_id(tmp_path):
    path = tmp_path / "config.toml"
    config.save(config.Config(device_id=0x00050021), path)
    c = _make(config_path=path)
    assert c._backlight.device_id == 0x00050021

    # Two writes of the same length: the content-hash signature still catches
    # the change (a stat-only mtime/size check could miss it on a coarse clock).
    config.save(config.Config(device_id=0x00050023), path)
    c._reload_config_if_changed()
    assert c._backlight.device_id == 0x00050023


def test_live_reload_keeps_watching_but_reapplies_cli_overrides(tmp_path):
    # A --timeout override must not silently undo a hand-edit to the *other*
    # keys: the file is still watched, and the override is re-applied on top.
    path = tmp_path / "config.toml"
    config.save(config.Config(timeout=3.0, on_level=1), path)
    c = Controller(
        config.Config(timeout=9.0, on_level=1).validated(),
        dry_run=True,
        config_path=path,
        overrides={"timeout": 9.0},
    )

    config.save(config.Config(timeout=3.0, on_level=3), path)
    c._reload_config_if_changed()

    assert c.config.on_level == 3       # the hand-edit landed
    assert c.config.timeout == 9.0      # the CLI override still wins


def test_live_reload_notifies_on_autostart_change(tmp_path):
    # FR-12: a flipped autostart key reaches the daemon's reconcile hook.
    path = tmp_path / "config.toml"
    config.save(config.Config(autostart=True), path)
    seen = []
    c = Controller(
        config.Config(autostart=True).validated(),
        dry_run=True,
        config_path=path,
        on_autostart_change=seen.append,
    )

    config.save(config.Config(autostart=False), path)
    c._reload_config_if_changed()
    assert seen == [False]

    # An unrelated edit must not re-fire it.
    config.save(config.Config(autostart=False, timeout=9.0), path)
    c._reload_config_if_changed()
    assert seen == [False]


def test_live_reload_device_id_change_forces_a_re_assert(tmp_path):
    path = tmp_path / "config.toml"
    config.save(config.Config(device_id=0x00050021), path)
    c = _make(config_path=path)
    c._hook.last_key = time.monotonic()
    c.tick()
    assert c._is_on is True

    config.save(config.Config(device_id=0x00050099), path)
    c._reload_config_if_changed()
    assert c._is_on is None  # forgotten, so the next tick re-issues set_level

    c.tick()
    assert c._is_on is True
    assert c._backlight.last_level == c.config.on_level


def test_no_watch_without_a_config_path(tmp_path):
    c = _make(timeout=5.0, config_path=None)
    c._reload_config_if_changed()  # must be an inert no-op
    assert c.config.timeout == 5.0


def test_reload_runs_on_every_20th_tick(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    config.save(config.Config(timeout=5.0), path)
    c = _make(timeout=5.0, config_path=path)
    c._hook.last_key = time.monotonic()

    calls = []
    monkeypatch.setattr(c, "_reload_config_if_changed", lambda: calls.append(c._tick_count))
    for _ in range(CONFIG_POLL_TICKS * 2):
        c.tick()
    assert calls == [CONFIG_POLL_TICKS, CONFIG_POLL_TICKS * 2]


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

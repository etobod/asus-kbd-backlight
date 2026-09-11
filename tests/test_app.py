"""CLI wiring in ``app.main`` that does not need the daemon to actually run:
the ``--settings`` / ``--install-task`` / ``--uninstall-task`` short-circuits,
the logging decision for transient sub-commands, and the autostart-ownership
rule.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from asus_kbd_backlight import app

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="app.main is Windows-only")


@pytest.fixture(autouse=True)
def _no_elevation(monkeypatch):
    # Every test here runs the post-elevation path; pretend we are already admin.
    monkeypatch.setattr(app, "is_admin", lambda: True)
    monkeypatch.setattr(app, "should_elevate", lambda *a, **k: False)
    monkeypatch.setattr(app, "_setup_logging", lambda *a, **k: None)


def test_settings_routes_to_the_window_without_touching_the_log_file(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        app, "_setup_logging",
        lambda debug, to_file=True: calls.update(debug=debug, to_file=to_file),
    )
    import asus_kbd_backlight.app_settings as app_settings

    monkeypatch.setattr(app_settings, "run", lambda path: 0)

    assert app.main(["--settings"]) == 0
    assert calls["to_file"] is False  # no rotating-log race with the daemon


def test_task_subcommands_do_not_touch_the_log_file(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        app, "_setup_logging",
        lambda debug, to_file=True: calls.update(to_file=to_file),
    )
    import asus_kbd_backlight.autostart as autostart

    monkeypatch.setattr(autostart, "reconcile", lambda *, enabled, **k: True)
    app.main(["--install-task"])
    assert calls["to_file"] is False


def test_set_does_not_touch_the_log_file_either(monkeypatch, tmp_path):
    # --set is documented (elevate.py) as belonging to the same "run in place,
    # don't touch daemon-owned resources" family as --settings / the task
    # subcommands; it must get the same log-file isolation they do.
    calls = {}
    monkeypatch.setattr(
        app, "_setup_logging",
        lambda debug, to_file=True: calls.update(to_file=to_file),
    )
    app.main(["--set", "1", "--dry-run", "--config", str(tmp_path / "config.toml")])
    assert calls["to_file"] is False


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["--settings"], True),
        (["--set", "1"], True),
        (["--install-task"], True),
        (["--uninstall-task"], True),
        ([], False),
        (["--dry-run"], False),
        (["--no-elevate"], False),
    ],
)
def test_is_one_shot_matches_mains_early_returns(argv, expected):
    assert app._is_one_shot(app._parse_args(argv)) is expected


def test_install_task_exit_code_follows_reconcile(monkeypatch):
    import asus_kbd_backlight.autostart as autostart

    seen = {}

    def _rec(*, enabled, **_):
        seen["enabled"] = enabled
        return True

    monkeypatch.setattr(autostart, "reconcile", _rec)
    assert app.main(["--install-task"]) == 0
    assert seen == {"enabled": True}

    monkeypatch.setattr(autostart, "reconcile", lambda *, enabled, **k: False)
    assert app.main(["--install-task"]) == 1  # a scheduler failure is reported


def test_uninstall_task_reconciles_false(monkeypatch):
    import asus_kbd_backlight.autostart as autostart

    seen = {}

    def _rec(*, enabled, **_):
        seen["enabled"] = enabled
        return True

    monkeypatch.setattr(autostart, "reconcile", _rec)
    assert app.main(["--uninstall-task"]) == 0
    assert seen == {"enabled": False}


def test_install_and_uninstall_task_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        app._parse_args(["--install-task", "--uninstall-task"])


@pytest.mark.parametrize(
    "argv,expected",
    [
        ([], True),
        (["--dry-run"], False),
        (["--config", "x.toml"], False),  # explicit config: hands-off the task
    ],
)
def test_manages_autostart_rule(argv, expected):
    assert app._manages_autostart(app._parse_args(argv), admin=True) is expected


def test_manages_autostart_requires_admin():
    # A --no-elevate dev run without admin rights must not attempt
    # Schedule.Service calls that are guaranteed to fail.
    assert app._manages_autostart(app._parse_args(["--no-elevate"]), admin=False) is False


class _QueuingThread:
    """Stand-in for threading.Thread that queues `target` instead of running
    it, so a test can control exactly when each "spawned" reconcile fires -
    deterministically reproducing a real race between two of them."""

    queued: list = []

    def __init__(self, target, **_kwargs):
        self._target = target

    def start(self):
        _QueuingThread.queued.append(self._target)


@pytest.fixture
def queued_threads(monkeypatch):
    _QueuingThread.queued = []
    monkeypatch.setattr(app.threading, "Thread", _QueuingThread)
    return _QueuingThread.queued


def test_autostart_reconciler_applies_a_single_change(queued_threads):
    calls = []
    on_change = app._make_autostart_reconciler(SimpleNamespace(reconcile=calls.append))
    on_change(True)
    assert len(queued_threads) == 1
    queued_threads[0]()
    assert calls == [True]


def test_autostart_reconciler_skips_a_request_superseded_before_it_ran(queued_threads):
    # Two toggles land within one poll window (FR-11/FR-12 interaction): the
    # thread for the *first* toggle must not clobber the second with a stale
    # value even if it happens to acquire the lock first.
    calls = []
    on_change = app._make_autostart_reconciler(SimpleNamespace(reconcile=calls.append))
    on_change(True)   # sequence 1
    on_change(False)  # sequence 2 - supersedes 1 before its thread ever runs
    assert len(queued_threads) == 2

    queued_threads[0]()  # the seq-1 thread: sees itself superseded, no-ops
    queued_threads[1]()  # the seq-2 thread: still current, applies

    assert calls == [False]


def test_set_one_shot_sets_the_level_and_exits_zero(caplog, tmp_path):
    # v0.1 gap: `--set LEVEL --dry-run` drives the NullBacklight once and returns
    # 0 without starting the hook, the worker, the tray or the message loop.
    # --config points at a file that does not exist, so this never reads the
    # real %APPDATA%\asus-kbd-backlight\config.toml (hermetic).
    cfg_path = str(tmp_path / "config.toml")
    with caplog.at_level("INFO"):
        assert app.main(["--set", "2", "--dry-run", "--config", cfg_path]) == 0
    assert any("set level 2" in r.message for r in caplog.records)


def test_set_one_shot_reports_a_backlight_error_as_exit_1(monkeypatch, tmp_path):
    from asus_kbd_backlight.backlight import BacklightError

    class _Broken:
        device_id = 0x00050021

        def set_level(self, level):
            raise BacklightError("no ATKACPI")

    monkeypatch.setattr(app, "get_backlight", lambda *a, **k: _Broken())
    cfg_path = str(tmp_path / "config.toml")
    assert app.main(["--set", "1", "--config", cfg_path]) == 1

"""FR-7: elevate once, never twice, never for the settings window.

The UAC prompt itself is manual (TEST-PLAN); what is pinned here is the
decision table around it and the shape of the child command line, because a
mistake there is either a prompt loop or a silently unelevated daemon.
"""

import os
import sys
import types
from argparse import Namespace

import pytest

from asus_kbd_backlight import elevate
from asus_kbd_backlight.app import _parse_args

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="elevation is Windows-only")


@pytest.fixture(autouse=True)
def _isolate_environ(monkeypatch):
    # relaunch_elevated() sets os.environ[AKB_ELEVATED]=1 (a best-effort
    # backstop - see elevate.py); give every test a private copy so that
    # never leaks forward.
    monkeypatch.setattr(os, "environ", dict(os.environ))


def _args(**over):
    base = {
        "settings": False, "no_elevate": False, "dry_run": False, "set": None,
        "install_task": False, "uninstall_task": False,
    }
    return Namespace(**{**base, **over})


def test_no_relaunch_when_already_admin():
    assert not elevate.should_elevate(_args(), admin=True, env={})


def test_relaunch_when_unelevated():
    assert elevate.should_elevate(_args(), admin=False, env={})


def test_no_relaunch_when_env_guard_set():
    # The guard must win even though we are not admin - this is what breaks a
    # prompt loop if the child somehow lost --no-elevate.
    assert not elevate.should_elevate(_args(), admin=False, env={elevate.ELEVATED_ENV: "1"})


@pytest.mark.parametrize("flag", ["settings", "no_elevate", "dry_run"])
def test_no_relaunch_for_opted_out_commands(flag):
    # --settings must never elevate (NFR-5); --dry-run touches no hardware.
    assert not elevate.should_elevate(_args(**{flag: True}), admin=False, env={})


def test_no_relaunch_for_set_probe():
    # `--set LEVEL` probes device_id / the level mapping; it must keep its
    # output on the caller's terminal, not spawn a detached elevated process.
    assert not elevate.should_elevate(_args(set=3), admin=False, env={})


@pytest.mark.parametrize("flag", ["install_task", "uninstall_task"])
def test_no_relaunch_for_task_subcommands(flag):
    # Same reason as --set: run in place and return a real exit code rather than
    # handing the result to a detached elevated process.
    assert not elevate.should_elevate(_args(**{flag: True}), admin=False, env={})


def test_child_command_appends_no_elevate_once():
    _, params = elevate.child_command(["--debug"])
    assert params.count(elevate.NO_ELEVATE_FLAG) == 1
    _, params = elevate.child_command(["--debug", elevate.NO_ELEVATE_FLAG])
    assert params.count(elevate.NO_ELEVATE_FLAG) == 1


def test_child_command_runs_the_module_when_not_frozen():
    exe, params = elevate.child_command(["--timeout", "5"])
    assert exe == sys.executable
    assert params[:2] == ["-m", "asus_kbd_backlight"]
    assert params[2:] == ["--timeout", "5", elevate.NO_ELEVATE_FLAG]


def test_child_command_runs_the_exe_when_frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    exe, params = elevate.child_command(["--debug"])
    assert exe == sys.executable
    assert params == ["--debug", elevate.NO_ELEVATE_FLAG]


class _ShellExecute:
    """Stand-in for the one Win32 call, so no test touches the real shell."""

    def __init__(self, rc):
        self.rc = rc
        self.calls = []

    def __call__(self, executable, params, directory=None):
        self.calls.append((executable, params, directory))
        return self.rc


@pytest.fixture
def shell_execute(monkeypatch):
    def _install(rc):
        stub = _ShellExecute(rc)
        monkeypatch.setattr(elevate, "_shell_execute_runas", stub)
        return stub

    return _install


def test_relaunch_starts_exactly_one_elevated_child(shell_execute):
    stub = shell_execute(42)  # > 32 == success
    assert elevate.relaunch_elevated(["--debug"]) == 0
    assert len(stub.calls) == 1
    executable, params, _ = stub.calls[0]
    assert executable == sys.executable
    assert elevate.NO_ELEVATE_FLAG in params


def test_relaunch_keeps_the_working_directory(shell_execute, monkeypatch, tmp_path):
    # A runas child otherwise starts in System32 and a relative --config would
    # name a different file there.
    monkeypatch.chdir(tmp_path)
    stub = shell_execute(42)
    elevate.relaunch_elevated(["--config", "my.toml"])
    assert stub.calls[0][2] == str(tmp_path)


def test_spawn_settings_forwards_the_config_path(monkeypatch):
    monkeypatch.setattr(elevate, "is_admin", lambda: False)
    calls = []
    monkeypatch.setattr(elevate.subprocess, "Popen", lambda argv, **kw: calls.append(argv))
    elevate.spawn_settings("my.toml")
    assert calls[0][calls[0].index("--config") + 1] == "my.toml"


def test_relaunch_sets_the_env_guard(shell_execute, monkeypatch):
    # Best-effort only - a runas-elevated child is not guaranteed to inherit
    # this (see elevate.py); pinned so the attempt itself doesn't regress.
    monkeypatch.setattr(os, "environ", dict(os.environ))
    os.environ.pop(elevate.ELEVATED_ENV, None)
    shell_execute(42)
    elevate.relaunch_elevated(["--debug"])
    assert os.environ.get(elevate.ELEVATED_ENV) == "1"


def test_declined_prompt_exits_non_zero(shell_execute, caplog):
    shell_execute(elevate.SE_ERR_ACCESSDENIED)
    with caplog.at_level("ERROR"):
        assert elevate.relaunch_elevated([]) != 0
    assert any("declined" in r.message for r in caplog.records)


def test_shell_execute_failure_exits_non_zero(shell_execute):
    shell_execute(2)  # SE_ERR_FNF
    assert elevate.relaunch_elevated([]) != 0


def test_dry_run_is_not_a_relaunch():
    # Integration check: the real parsed namespace and should_elevate agree.
    assert elevate.should_elevate(_parse_args(["--dry-run"]), admin=False, env={}) is False


def test_cli_exposes_the_elevation_flags():
    args = _parse_args(["--settings", "--no-elevate"])
    assert args.settings and args.no_elevate


# --- de-elevated settings spawn (NFR-5) ----------------------------------


def test_windowless_launch_command_from_source_runs_the_module_via_pythonw(fake_exe):
    # No console window behind the settings window (the swap rule itself is
    # pinned in test_paths; here: that the launch command uses it).
    tmp = fake_exe("python.exe", "pythonw.exe")
    exe, params = elevate.launch_command(["--settings", "--no-elevate"], windowless=True)
    assert exe == str(tmp / "pythonw.exe")
    assert params == ["-m", "asus_kbd_backlight", "--settings", "--no-elevate"]


def test_windowless_launch_command_when_frozen_uses_the_windowed_twin(fake_exe):
    tmp = fake_exe("asus-kbd-backlight-debug.exe", "asus-kbd-backlight.exe", frozen=True)
    exe, params = elevate.launch_command(["--settings", "--no-elevate"], windowless=True)
    assert exe == str(tmp / "asus-kbd-backlight.exe")
    assert params == ["--settings", "--no-elevate"]


def test_relaunch_command_is_not_redirected_to_the_windowless_program(fake_exe):
    # Only the settings child / logon task switch programs; the daemon's own
    # elevated re-launch keeps running the exact executable the user started.
    tmp = fake_exe("asus-kbd-backlight-debug.exe", "asus-kbd-backlight.exe", frozen=True)
    exe, _ = elevate.child_command(["--debug"])
    assert exe == str(tmp / "asus-kbd-backlight-debug.exe")


def test_spawn_settings_unelevated_uses_a_plain_popen(monkeypatch):
    monkeypatch.setattr(elevate, "is_admin", lambda: False)
    calls = []
    monkeypatch.setattr(elevate.subprocess, "Popen", lambda argv, **kw: calls.append(argv))
    # If it ever reached for runas / an Explorer handoff that would be a bug.
    monkeypatch.setattr(
        elevate, "_shell_execute_runas",
        lambda *a: pytest.fail("settings spawn must never elevate"),
    )
    monkeypatch.setattr(
        elevate, "_spawn_via_explorer",
        lambda *a: pytest.fail("no Explorer handoff needed when already unelevated"),
    )
    elevate.spawn_settings()
    assert len(calls) == 1
    assert "--settings" in calls[0] and elevate.NO_ELEVATE_FLAG in calls[0]


def test_spawn_settings_elevated_hands_off_to_explorer_never_runas(monkeypatch):
    monkeypatch.setattr(elevate, "is_admin", lambda: True)
    monkeypatch.setattr(
        elevate, "_shell_execute_runas",
        lambda *a: pytest.fail("runas raises integrity - the opposite of NFR-5"),
    )
    monkeypatch.setattr(
        elevate.subprocess, "Popen",
        lambda *a, **k: pytest.fail("an elevated Popen would inherit the admin token"),
    )
    seen = {}
    monkeypatch.setattr(
        elevate, "_spawn_via_explorer",
        lambda exe, params: seen.update(executable=exe, params=params),
    )
    elevate.spawn_settings()
    assert "--settings" in seen["params"] and elevate.NO_ELEVATE_FLAG in seen["params"]


def test_spawn_settings_passes_the_config_path(monkeypatch):
    monkeypatch.setattr(elevate, "is_admin", lambda: False)
    calls = []
    monkeypatch.setattr(elevate.subprocess, "Popen", lambda argv, **kw: calls.append(argv))
    elevate.spawn_settings(r"C:\somewhere\config.toml")
    assert "--config" in calls[0]
    assert calls[0][calls[0].index("--config") + 1] == r"C:\somewhere\config.toml"


def test_spawn_settings_elevated_survives_an_explorer_handoff_failure(monkeypatch, caplog):
    monkeypatch.setattr(elevate, "is_admin", lambda: True)

    def _boom(exe, params):
        raise OSError("could not create the settings shortcut")

    monkeypatch.setattr(elevate, "_spawn_via_explorer", _boom)
    with caplog.at_level("ERROR"):
        elevate.spawn_settings()  # must not raise
    assert any("settings window" in r.message for r in caplog.records)


class _FakeShortcut:
    def __init__(self):
        self.TargetPath = None
        self.Arguments = None
        self.WorkingDirectory = None
        self.WindowStyle = None
        self.saved = False

    def Save(self):
        self.saved = True


class _FakeWSH:
    def __init__(self):
        self.created = []

    def CreateShortCut(self, path):
        sc = _FakeShortcut()
        self.created.append((path, sc))
        return sc


def _install_fake_win32com(monkeypatch, dispatch):
    """Stub sys.modules so ``import win32com.client`` inside
    :func:`elevate._spawn_via_explorer` resolves to a fake, no real COM/shell
    touched. Both ``win32com`` and ``win32com.client`` are patched, because
    ``import a.b`` binds the *parent* name and reads its ``.b`` attribute."""
    fake_client = types.SimpleNamespace(Dispatch=dispatch)
    fake_win32com = types.SimpleNamespace(client=fake_client)
    monkeypatch.setitem(sys.modules, "win32com", fake_win32com)
    monkeypatch.setitem(sys.modules, "win32com.client", fake_client)


@pytest.fixture
def fake_win32com(monkeypatch):
    wsh = _FakeWSH()
    _install_fake_win32com(monkeypatch, lambda name: wsh)
    return wsh


def test_spawn_via_explorer_writes_a_shortcut_and_hands_it_to_explorer(
    monkeypatch, fake_win32com, tmp_path
):
    monkeypatch.chdir(tmp_path)
    calls = []
    monkeypatch.setattr(elevate.subprocess, "Popen", lambda argv, **kw: calls.append(argv))
    monkeypatch.setattr(elevate.os, "remove", lambda path: None)  # never touch the real temp dir

    elevate._spawn_via_explorer(
        r"C:\python\python.exe", ["-m", "asus_kbd_backlight", "--settings", "--no-elevate"]
    )

    assert len(fake_win32com.created) == 1
    lnk_path, shortcut = fake_win32com.created[0]
    assert lnk_path.endswith(".lnk")
    assert shortcut.saved is True
    assert shortcut.TargetPath == r"C:\python\python.exe"
    assert "--settings" in shortcut.Arguments and "--no-elevate" in shortcut.Arguments
    # The child starts where the daemon runs, so a relative --config names the
    # same file (without this Explorer starts it in the target's folder).
    assert shortcut.WorkingDirectory == str(tmp_path)

    # Explorer, not us, does the actual launch - the "elevated Popen" trap
    # NFR-5 exists for is only reachable via CreateProcess-family calls, and
    # explorer.exe (already running, medium integrity) is what we hand off to.
    assert calls == [["explorer.exe", lnk_path]]


def test_spawn_via_explorer_wraps_a_com_failure_as_oserror(monkeypatch):
    def _boom(name):
        raise RuntimeError("pywintypes.com_error stand-in")

    _install_fake_win32com(monkeypatch, _boom)
    with pytest.raises(OSError, match="settings shortcut"):
        elevate._spawn_via_explorer("C:\\a.exe", ["--settings"])


def test_spawn_via_explorer_cleans_up_the_shortcut_on_a_launch_failure(monkeypatch, fake_win32com):
    removed = []
    monkeypatch.setattr(elevate.os, "remove", lambda path: removed.append(path))
    monkeypatch.setattr(
        elevate.subprocess, "Popen",
        lambda *a, **k: (_ for _ in ()).throw(OSError("explorer.exe not found")),
    )

    with pytest.raises(OSError):
        elevate._spawn_via_explorer("C:\\a.exe", ["--settings"])
    assert len(removed) == 1

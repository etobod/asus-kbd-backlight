"""Self-elevation (FR-7).

Backlight control writes through ATKACPI and the low-level hook has to see keys
typed into elevated windows, so the daemon needs Administrator rights. Both
the frozen builds and a source checkout run ``asInvoker`` (no
``requireAdministrator`` manifest), so ``main()`` re-launches itself once
through the ``runas`` verb. A manifest would make *every* launch of the exe
elevate - including the settings window, which must stay unelevated (NFR-5)
and is started from the very same exe with ``--settings``.

The guard against a prompt loop is ``--no-elevate`` on the relaunched command
line - ``relaunch_elevated`` always appends it, and :func:`should_elevate`
always honours it. ``relaunch_elevated`` also sets ``AKB_ELEVATED=1`` in
*this* process's environment before calling ``ShellExecuteW("runas", ...)``,
but that is a best-effort backstop, not a second guaranteed guard: a
``runas``-elevated child is started by the elevation broker (AppInfo /
``consent.exe``), not as a direct child of this process, so Windows does not
promise it inherits environment variables set here. Never remove
``--no-elevate`` from ``child_command`` on the strength of the env var alone.

Nothing here elevates the settings window: it is spawned with ``--settings``,
which :func:`should_elevate` never elevates (NFR-5). ``--set`` (the device-id /
level probe) and ``--dry-run`` opt out too, so their output stays on the
caller's terminal instead of a detached elevated process.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from typing import Protocol

log = logging.getLogger(__name__)

ELEVATED_ENV = "AKB_ELEVATED"
NO_ELEVATE_FLAG = "--no-elevate"

SW_SHOWNORMAL = 1
# ShellExecuteW reports failure as a "handle" <= 32. Declining the UAC prompt
# surfaces as SE_ERR_ACCESSDENIED in that return value (ERROR_CANCELLED, 1223,
# is a GetLastError code and never appears here).
_SHELL_EXECUTE_MAX_ERROR = 32
SE_ERR_ACCESSDENIED = 5

SETTINGS_FLAG = "--settings"


class _Args(Protocol):
    """The subset of the parsed CLI namespace elevation cares about."""

    settings: bool
    no_elevate: bool
    dry_run: bool
    set: int | None
    install_task: bool
    uninstall_task: bool


def is_admin() -> bool:
    """True when the process holds an elevated token (or is not on Windows)."""
    if sys.platform != "win32":
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (OSError, AttributeError):
        return False


def should_elevate(
    args: _Args,
    *,
    admin: bool | None = None,
    env: Mapping[str, str] | None = None,
) -> bool:
    """Whether ``main()`` must re-launch itself elevated.

    Pure decision function — no side effects, so the whole truth table is
    unit-testable without a UAC prompt.
    """
    if sys.platform != "win32":
        return False
    if getattr(args, "settings", False) or getattr(args, "no_elevate", False):
        return False
    if getattr(args, "dry_run", False):
        return False  # touches no hardware; demanding UAC would be rude
    if getattr(args, "set", None) is not None:
        # The device-id / level probe: elevating would spawn a detached process
        # and the caller would never see the result or the BacklightError.
        return False
    if getattr(args, "install_task", False) or getattr(args, "uninstall_task", False):
        # Same reason as --set: run in place and report a real exit code, don't
        # hand the result to a detached elevated process the caller can't read.
        return False
    if env is None:
        env = os.environ
    if env.get(ELEVATED_ENV) == "1":
        return False
    return not (is_admin() if admin is None else admin)


def launch_command(argv: Sequence[str], *, windowless: bool = False) -> tuple[str, list[str]]:
    """The ``(executable, parameters)`` pair that starts this program with ``argv``.

    Frozen, the executable is the exe itself; from source it is the interpreter
    running ``-m asus_kbd_backlight``. ``windowless`` swaps in the console-less
    program (``paths.windowless_executable``: the windowed exe rather than the
    debug build, ``pythonw.exe`` rather than ``python.exe``) for children the
    user should never see a console for - the settings window, the logon task.
    """
    if windowless:
        from asus_kbd_backlight.paths import windowless_executable

        executable = windowless_executable()
    else:
        executable = sys.executable
    params = list(argv)
    if getattr(sys, "frozen", False):
        return executable, params
    return executable, ["-m", "asus_kbd_backlight", *params]


def child_command(argv: Sequence[str]) -> tuple[str, list[str]]:
    """The ``(executable, parameters)`` pair for the elevated re-launch.

    ``--no-elevate`` is appended so the child can never prompt again. It keeps
    the exact program the user started (no ``windowless`` swap).
    """
    params = list(argv)
    if NO_ELEVATE_FLAG not in params:
        params.append(NO_ELEVATE_FLAG)
    return launch_command(params)


def _shell_execute_runas(executable: str, params: str, directory: str | None = None) -> int:
    """``ShellExecuteW`` with the ``runas`` verb. Returns its raw result.

    ``directory`` becomes the child's working directory. Without it a
    ``runas``-elevated process starts in ``C:\\Windows\\System32``, so any
    relative path on the command line (``--config my.toml``) would point at a
    different file in the elevated child than in the process the user started.

    The return is an ``HINSTANCE``, i.e. pointer-width. Without an explicit
    ``restype`` ctypes assumes ``c_int`` and truncates it on 64-bit, which can
    turn a successful launch into an apparent failure - the same trap that once
    broke ``SetWindowsHookEx`` (see DESIGN). Declaring it is suppressed rather
    than required so tests can substitute a plain callable.
    """
    fn = ctypes.windll.shell32.ShellExecuteW
    with contextlib.suppress(AttributeError, TypeError):
        fn.restype = ctypes.c_void_p
    return int(fn(None, "runas", executable, params, directory, SW_SHOWNORMAL) or 0)


def relaunch_elevated(argv: Sequence[str]) -> int:
    """Start an elevated copy of this program. Returns an exit code for main().

    0 means the child was started and this process should exit quietly; any
    other value means we could not elevate and the caller must not continue in
    a half-working, unelevated state.
    """
    executable, params = child_command(argv)
    # Best-effort backstop, not a guaranteed second guard: a runas-elevated
    # child is launched by the elevation broker, not as our direct child, so
    # Windows does not promise it sees this. --no-elevate on the command line
    # (child_command, always appended) is the guard that actually stops a
    # prompt loop.
    os.environ[ELEVATED_ENV] = "1"
    log.info("requesting Administrator rights (a UAC prompt will appear)")
    try:
        rc = _shell_execute_runas(executable, subprocess.list2cmdline(params), os.getcwd())
    except OSError as exc:
        log.error("could not request elevation: %s", exc)
        return 1
    if rc > _SHELL_EXECUTE_MAX_ERROR:
        return 0
    if rc == SE_ERR_ACCESSDENIED:
        log.error("Administrator rights were declined - asus-kbd-backlight cannot run")
    else:
        log.error("could not re-launch elevated (ShellExecuteW returned %d)", rc)
    return 1


# --- de-elevated spawn for the settings window (NFR-5) ---------------------
#
# The daemon runs elevated, and a plain ``subprocess.Popen`` from an elevated
# parent hands the child the *same* admin token - so the settings window would
# run elevated and write ``config.toml`` with an admin-owned ACL. NFR-5 says the
# window and every config write stay unelevated. When we are already admin we
# therefore hand the launch to Explorer (below); when we are not elevated a
# plain ``Popen`` is already correct.


def _spawn_via_explorer(executable: str, params: list[str]) -> None:
    """Launch ``executable params...`` at Explorer's (medium) integrity.

    A first attempt duplicated the shell's token and used
    ``CreateProcessWithTokenW`` directly - the textbook Win32 mechanism, but
    also a well-known "token theft" pattern security software watches for, and
    it kept failing with ``ERROR_ACCESS_DENIED`` even with every privilege the
    API documents enabled (see git history / CHANGELOG). This is the
    Microsoft-documented alternative (Aaron Margosis's "ShellExecute from an
    explorer window" pattern): write a ``.lnk`` shortcut carrying the real
    command line, then ask **Explorer itself** to open it
    (``explorer.exe <path-to-lnk>``). Explorer is single-instance and already
    running at medium integrity, so the *existing* Explorer process does the
    actual launch - no token duplication, no elevated privilege needed here at
    all.

    Raises ``OSError`` if the shortcut can't be created or Explorer can't be
    asked to open it (``pywintypes.com_error`` from the COM calls is wrapped
    into ``OSError`` too, so callers only need to catch one type).
    """
    import tempfile
    import uuid

    from asus_kbd_backlight.backlight import _co_initialize

    _co_initialize()  # win32com needs COM initialised on this (spawn) thread
    import win32com.client

    lnk_path = os.path.join(tempfile.gettempdir(), f"akb-settings-{uuid.uuid4().hex}.lnk")
    try:
        wsh = win32com.client.Dispatch("WScript.Shell")
        shortcut = wsh.CreateShortCut(lnk_path)
        shortcut.TargetPath = executable
        shortcut.Arguments = subprocess.list2cmdline(params)
        # Our working directory, as the runas relaunch keeps it too: a relative
        # path means the same file in the settings window as in the daemon.
        shortcut.WorkingDirectory = os.getcwd()
        shortcut.WindowStyle = SW_SHOWNORMAL
        shortcut.Save()
    except Exception as exc:  # noqa: BLE001 - pywintypes.com_error isn't an OSError
        raise OSError(f"could not create the settings shortcut: {exc}") from exc

    try:
        subprocess.Popen(["explorer.exe", lnk_path], close_fds=True)
    except OSError:
        with contextlib.suppress(OSError):
            os.remove(lnk_path)
        raise
    else:
        # Explorer reads the .lnk asynchronously (it hands the request to the
        # already-running instance and returns); give it a few seconds before
        # cleaning up rather than racing it. Best-effort - a leftover .lnk in
        # %TEMP% is harmless.
        import threading
        import time

        def _cleanup() -> None:
            time.sleep(10)
            with contextlib.suppress(OSError):
                os.remove(lnk_path)

        threading.Thread(target=_cleanup, name="settings-lnk-cleanup", daemon=True).start()


def spawn_settings(config_path: object | None = None) -> None:
    """Open the settings window as an **unelevated** child process (NFR-5).

    From an elevated daemon this hands the launch to Explorer (medium
    integrity); run unelevated (a dev running ``--no-elevate``) a plain
    ``Popen`` is already right. Never uses the ``runas`` verb - that would
    *raise* the integrity, the opposite of what NFR-5 needs.

    Creating the shortcut + spawning can take a beat; the caller invokes this
    off the message-pump thread (see ``app._launch_settings``) so a slow spawn
    can never trip ``LowLevelHooksTimeout`` and drop keystrokes.
    """
    argv: list[str] = [SETTINGS_FLAG, NO_ELEVATE_FLAG]
    if config_path is not None:
        argv += ["--config", str(config_path)]
    executable, params = launch_command(argv, windowless=True)

    if not is_admin():
        subprocess.Popen([executable, *params], close_fds=True)
        return

    try:
        _spawn_via_explorer(executable, params)
    except OSError as exc:
        log.error("could not open the settings window unelevated: %s", exc)

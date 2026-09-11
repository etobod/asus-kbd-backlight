"""Self-elevation (FR-7).

Backlight control writes through ATKACPI and the low-level hook has to see keys
typed into elevated windows, so the daemon needs Administrator rights. The
frozen build gets them from the ``--uac-admin`` manifest; running from source
there is no manifest, so ``main()`` re-launches itself once through the
``runas`` verb.

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


def _command(argv: Sequence[str]) -> tuple[str, list[str]]:
    """The ``(executable, parameters)`` pair for re-launching this program.

    Frozen, the executable is the exe itself; from source it is the
    interpreter running ``-m asus_kbd_backlight``.
    """
    params = list(argv)
    if getattr(sys, "frozen", False):
        return sys.executable, params
    return sys.executable, ["-m", "asus_kbd_backlight", *params]


def child_command(argv: Sequence[str]) -> tuple[str, list[str]]:
    """The ``(executable, parameters)`` pair for the elevated re-launch.

    ``--no-elevate`` is appended so the child can never prompt again.
    """
    params = list(argv)
    if NO_ELEVATE_FLAG not in params:
        params.append(NO_ELEVATE_FLAG)
    return _command(params)


def _shell_execute_runas(executable: str, params: str) -> int:
    """``ShellExecuteW`` with the ``runas`` verb. Returns its raw result.

    The return is an ``HINSTANCE``, i.e. pointer-width. Without an explicit
    ``restype`` ctypes assumes ``c_int`` and truncates it on 64-bit, which can
    turn a successful launch into an apparent failure - the same trap that once
    broke ``SetWindowsHookEx`` (see DESIGN). Declaring it is suppressed rather
    than required so tests can substitute a plain callable.
    """
    fn = ctypes.windll.shell32.ShellExecuteW
    with contextlib.suppress(AttributeError, TypeError):
        fn.restype = ctypes.c_void_p
    return int(fn(None, "runas", executable, params, None, SW_SHOWNORMAL) or 0)


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
        rc = _shell_execute_runas(executable, subprocess.list2cmdline(params))
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
# therefore launch it with a token borrowed from the shell (Explorer runs at
# medium integrity), via ``CreateProcessWithTokenW``; when we are not elevated a
# plain ``Popen`` is already correct.


def settings_command(argv: Sequence[str]) -> tuple[str, list[str]]:
    """The ``(executable, parameters)`` pair that opens the settings window."""
    return _command(argv)


def _shell_token() -> int:
    """A primary token duplicated from the running shell (medium integrity).

    Raises ``OSError`` if the shell is not reachable or the duplication fails.
    Kept behind its own function so :func:`spawn_settings` is testable without
    touching Win32.
    """
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    advapi32 = ctypes.windll.advapi32

    # HANDLE-returning calls need an explicit restype or the 64-bit value is
    # truncated to c_int - the same trap DESIGN.md documents for
    # SetWindowsHookExW / ShellExecuteW. GetShellWindow returns an HWND, which
    # Microsoft guarantees is 32-bit-safe even cross-bitness, but OpenProcess's
    # HANDLE carries no such guarantee.
    user32.GetShellWindow.restype = wintypes.HWND
    kernel32.OpenProcess.restype = wintypes.HANDLE

    hwnd = user32.GetShellWindow()
    if not hwnd:
        raise OSError("no shell window; cannot lower integrity")
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

    PROCESS_QUERY_INFORMATION = 0x0400
    TOKEN_DUPLICATE = 0x0002
    TOKEN_ASSIGN_PRIMARY = 0x0001
    TOKEN_QUERY = 0x0008
    SECURITY_IMPERSONATION = 2
    TOKEN_PRIMARY = 1

    hproc = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid.value)
    if not hproc:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        htok = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(hproc, TOKEN_DUPLICATE, ctypes.byref(htok)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            primary = wintypes.HANDLE()
            access = TOKEN_ASSIGN_PRIMARY | TOKEN_DUPLICATE | TOKEN_QUERY
            if not advapi32.DuplicateTokenEx(
                htok, access, None, SECURITY_IMPERSONATION, TOKEN_PRIMARY,
                ctypes.byref(primary),
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            return primary.value
        finally:
            kernel32.CloseHandle(htok)
    finally:
        kernel32.CloseHandle(hproc)


def _spawn_with_token(token: int, executable: str, command_line: str) -> None:
    """``CreateProcessWithTokenW`` on ``token``; raises ``OSError`` on failure."""
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    advapi32 = ctypes.windll.advapi32

    class _STARTUPINFO(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.c_void_p),
            ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class _PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD),
        ]

    si = _STARTUPINFO()
    si.cb = ctypes.sizeof(si)
    pi = _PROCESS_INFORMATION()
    # dwLogonFlags = 0: do NOT pass LOGON_WITH_PROFILE. Loading the profile hive
    # can take hundreds of ms, and this runs (off-thread, but still) near the
    # message pump; the settings window only needs %APPDATA%, already in the
    # environment. The CreateProcess*W family may write into lpCommandLine, so
    # hand it a mutable buffer, never the interned str.
    CREATE_UNICODE_ENVIRONMENT = 0x00000400
    cmd_buf = ctypes.create_unicode_buffer(command_line)
    try:
        ok = advapi32.CreateProcessWithTokenW(
            wintypes.HANDLE(token), 0, executable, cmd_buf,
            CREATE_UNICODE_ENVIRONMENT, None, None, ctypes.byref(si), ctypes.byref(pi),
        )
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
        kernel32.CloseHandle(pi.hProcess)
        kernel32.CloseHandle(pi.hThread)
    finally:
        kernel32.CloseHandle(token)


def spawn_settings(config_path: object | None = None) -> None:
    """Open the settings window as an **unelevated** child process (NFR-5).

    From an elevated daemon this borrows the shell's medium-integrity token; run
    unelevated (a dev running ``--no-elevate``) a plain ``Popen`` is already
    right. Never uses the ``runas`` verb - that would *raise* the integrity, the
    opposite of what NFR-5 needs.

    Token duplication + process creation can take a beat; the caller invokes
    this off the message-pump thread (see ``app._launch_settings``) so a slow
    spawn can never trip ``LowLevelHooksTimeout`` and drop keystrokes.
    """
    argv: list[str] = [SETTINGS_FLAG, NO_ELEVATE_FLAG]
    if config_path is not None:
        argv += ["--config", str(config_path)]
    executable, params = settings_command(argv)

    if not is_admin():
        subprocess.Popen([executable, *params], close_fds=True)
        return

    command_line = subprocess.list2cmdline([executable, *params])
    try:
        token = _shell_token()
        _spawn_with_token(token, executable, command_line)
    except OSError as exc:
        log.error("could not open the settings window unelevated: %s", exc)

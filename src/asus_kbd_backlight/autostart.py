"""Start-with-Windows via a Task Scheduler logon task (FR-12, R-8).

A ``requireAdministrator`` manifest rules out the Startup folder and
``HKCU\\Run`` for an elevated program, so the supported autostart is a Task
Scheduler entry with *Run with highest privileges* — it starts the daemon
elevated at logon with **no** UAC prompt.

The **elevated daemon** owns the task: :func:`reconcile` is called at startup
and after every live config reload, and creates / refreshes / deletes the entry
to match ``cfg.autostart``. The unelevated settings window never touches the
scheduler (NFR-5) — it only flips the ``autostart`` key in ``config.toml`` and
the daemon reacts.

Every Task Scheduler call goes through ``win32com`` ``Schedule.Service``; no new
dependency (pywin32 is already required) and no ``schtasks.exe`` string
quoting. The COM object is injectable so :func:`reconcile`'s create/refresh/
delete/no-op decision is unit-tested against a fake service; the actual logon
is manual (TEST-PLAN).
"""

from __future__ import annotations

import logging
import os
import sys

log = logging.getLogger(__name__)

TASK_NAME = "asus-kbd-backlight"
TASK_FOLDER = "\\"

# TaskScheduler enum values (winnt.h / taskschd.h), inlined to avoid importing
# the type library just for six constants.
_TASK_TRIGGER_LOGON = 9
_TASK_ACTION_EXEC = 0
_TASK_CREATE_OR_UPDATE = 6
_TASK_LOGON_INTERACTIVE_TOKEN = 3
_TASK_RUNLEVEL_HIGHEST = 1


def _connect() -> object:
    """A connected ``Schedule.Service`` COM object."""
    import win32com.client  # lazy: keeps import-time deps off non-Windows

    from asus_kbd_backlight.backlight import _co_initialize

    _co_initialize()  # no-op / S_FALSE if the thread already did it
    svc = win32com.client.Dispatch("Schedule.Service")
    svc.Connect()
    return svc


def _current_user() -> str:
    domain = os.environ.get("USERDOMAIN")
    user = os.environ.get("USERNAME")
    if domain and user:
        return f"{domain}\\{user}"
    return user or ""


def action_target() -> tuple[str, str]:
    """``(program, arguments)`` the task should run.

    Frozen: the exe itself. From source: ``pythonw -m asus_kbd_backlight`` so no
    console flashes at logon. Recomputed on every reconcile so moving the exe
    (or switching Python) is self-healing.
    """
    if getattr(sys, "frozen", False):
        return sys.executable, ""
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    exe = pythonw if os.path.isfile(pythonw) else sys.executable
    return exe, "-m asus_kbd_backlight"


def _task_exists(folder: object) -> bool:
    try:
        folder.GetTask(TASK_NAME)
        return True
    except Exception:  # noqa: BLE001 - COM raises for "not found"; treat as absent
        return False


def _populate(task_def: object) -> None:
    """Fill a fresh ``ITaskDefinition`` in place."""
    program, arguments = action_target()

    task_def.RegistrationInfo.Description = (
        "Keyboard-only idle timeout for the ASUS keyboard backlight."
    )
    task_def.RegistrationInfo.Author = TASK_NAME

    task_def.Principal.RunLevel = _TASK_RUNLEVEL_HIGHEST  # no logon-time UAC
    task_def.Principal.LogonType = _TASK_LOGON_INTERACTIVE_TOKEN

    trigger = task_def.Triggers.Create(_TASK_TRIGGER_LOGON)
    user = _current_user()
    if user:
        trigger.UserId = user

    action = task_def.Actions.Create(_TASK_ACTION_EXEC)
    action.Path = program
    if arguments:
        action.Arguments = arguments

    settings = task_def.Settings
    settings.DisallowStartIfOnBatteries = False
    settings.StopIfGoingOnBatteries = False
    settings.ExecutionTimeLimit = "PT0S"  # no time limit
    settings.StartWhenAvailable = True


def _register(service: object, folder: object) -> None:
    task_def = service.NewTask(0)
    _populate(task_def)
    folder.RegisterTaskDefinition(
        TASK_NAME, task_def, _TASK_CREATE_OR_UPDATE, None, None,
        _TASK_LOGON_INTERACTIVE_TOKEN,
    )


def task_exists(*, service: object | None = None) -> bool:
    """Whether the autostart task is currently registered."""
    try:
        svc = service if service is not None else _connect()
        return _task_exists(svc.GetFolder(TASK_FOLDER))
    except Exception as exc:  # noqa: BLE001
        log.warning("could not query the autostart task: %s", exc)
        return False


def reconcile(enabled: bool, *, service: object | None = None) -> bool:
    """Make the registered task match ``enabled``. Returns whether it succeeded.

    ``enabled`` → create it, or refresh its action path if it already exists
    (self-heals a moved exe). ``not enabled`` → delete it if present. Any COM
    failure is logged and swallowed (returning ``False``): a scheduler hiccup
    must never take the daemon down (R-8), but an explicit
    ``--install-task`` / ``--uninstall-task`` needs the real exit code.
    """
    try:
        svc = service if service is not None else _connect()
        folder = svc.GetFolder(TASK_FOLDER)
        exists = _task_exists(folder)
        if enabled:
            _register(svc, folder)
            log.info("autostart task %s", "refreshed" if exists else "created")
        elif exists:
            folder.DeleteTask(TASK_NAME, 0)
            log.info("autostart task removed")
    except Exception as exc:  # noqa: BLE001
        log.warning("could not reconcile the autostart task: %s", exc)
        return False
    return True

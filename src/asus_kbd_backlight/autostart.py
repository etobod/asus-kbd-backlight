"""Start-with-Windows via a Task Scheduler logon task (FR-12, R-8).

The daemon needs Administrator rights, and the Startup folder / ``HKCU\\Run``
can only start it unelevated - it would then ask for UAC at every logon. The
supported autostart is a Task Scheduler entry with *Run with highest
privileges* — it starts the daemon elevated at logon with **no** UAC prompt.

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
import subprocess

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
_TASK_PRIORITY_NORMAL = 4  # ITaskSettings.Priority: 0 realtime .. 10 idle, default 7


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

    Frozen: the windowed exe (even when the daemon itself is the console debug
    build). From source: ``pythonw -m asus_kbd_backlight``. Either way no
    console flashes at logon (``paths.windowless_executable``). Recomputed on
    every reconcile so moving the exe (or switching Python) is self-healing.
    """
    from asus_kbd_backlight.elevate import launch_command

    executable, params = launch_command([], windowless=True)
    return executable, subprocess.list2cmdline(params)


# ITaskFolder.GetTasks flag: include hidden tasks in the listing.
_TASK_ENUM_HIDDEN = 1


def _task_exists(folder: object) -> bool:
    """Whether the task is registered - decided by *listing* the folder, not by
    catching ``GetTask``'s "not found": pywin32 reports that as
    ``DISP_E_EXCEPTION`` with the real code tucked into the excepinfo, a shape
    an earlier version got wrong (and then the logon task could never be
    created). A listing needs no error classification at all, so every
    exception here is a genuine failure (access denied, the service
    unreachable) and propagates: :func:`reconcile` reports it rather than
    skipping a delete and claiming success. Task names are case-insensitive."""
    tasks = folder.GetTasks(_TASK_ENUM_HIDDEN)
    wanted = TASK_NAME.casefold()
    return any(tasks.Item(i).Name.casefold() == wanted for i in range(1, tasks.Count + 1))


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
    # Task Scheduler's default is 7 (below normal). The daemon services the
    # low-level keyboard hook; starved of CPU under load it can blow
    # LowLevelHooksTimeout and miss keystrokes. 4 == normal priority.
    settings.Priority = _TASK_PRIORITY_NORMAL


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

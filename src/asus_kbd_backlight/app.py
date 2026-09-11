"""Wiring: keyboard-idle hook -> state machine -> backlight.

The hook thread does no work beyond stamping a timestamp (NFR-1). A separate
worker thread compares that timestamp against the timeout and issues a control
call *only* when the desired state changes (NFR-2).
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import logging.handlers
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from asus_kbd_backlight import __version__, config
from asus_kbd_backlight.backlight import LEVEL_OFF, BacklightError, get_backlight
from asus_kbd_backlight.elevate import is_admin, relaunch_elevated, should_elevate
from asus_kbd_backlight.hook import KeyboardIdleHook, pump_messages

POLL_INTERVAL = 0.05  # 50 ms -> well inside the <500 ms turn-off target
FAIL_RETRY_INTERVAL = 3.0  # while backlight control is failing, don't re-hit WMI every tick
CONFIG_POLL_TICKS = 20  # re-read config.toml every 20th tick (~1 s) for live reload (FR-11)
WM_QUIT = 0x0012

log = logging.getLogger("asus_kbd_backlight")

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if sys.platform == "win32" else None
_user32 = ctypes.WinDLL("user32", use_last_error=True) if sys.platform == "win32" else None


class Controller:
    def __init__(
        self,
        cfg: config.Config,
        *,
        dry_run: bool = False,
        config_path: Path | None = None,
        overrides: dict[str, object] | None = None,
        on_autostart_change: Callable[[bool], object] | None = None,
    ) -> None:
        self._cfg = cfg
        self._backlight = get_backlight(cfg.device_id, dry_run=dry_run)
        self._hook = KeyboardIdleHook()
        self._is_on: bool | None = None
        self._last_error: str | None = None
        self._last_attempt = 0.0
        # Set from the message-pump thread (tray Pause), read by the worker.
        # pause() only flips this flag; the worker's next tick issues the
        # backlight call, so every COM/backlight call stays on the worker
        # thread that owns the CoInitialize apartment and the cached WMI
        # objects.
        self._paused = False
        # Live config reload (FR-11): the worker re-reads config_path ~1x/s and
        # swaps _cfg under this lock. None disables the watch. CLI overrides
        # (--timeout / --on-level) are re-applied on top of every reload so a
        # hand-edit to the *other* keys is still picked up.
        self._cfg_path = config_path
        self._overrides = dict(overrides or {})
        # Called (on the worker thread) with the new value when a reload flips
        # cfg.autostart, so the elevated daemon can reconcile the scheduler
        # task (FR-12). None in --dry-run and off Windows.
        self._on_autostart_change = on_autostart_change
        self._cfg_lock = threading.Lock()
        self._cfg_raw = self._read_config()
        self._reload_error: str | None = None
        self._tick_count = 0
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._run, name="backlight-worker", daemon=True)

    @property
    def config(self) -> config.Config:
        with self._cfg_lock:
            return self._cfg

    def _read_config(self) -> bytes | None:
        """The watched file's raw bytes, or None if it is not there.

        The reload compares (and parses) the bytes themselves - not
        ``(mtime, size)``, which two same-length writes on a coarse clock share,
        and not a re-open at parse time, which a delete-and-recreate editor can
        turn into a silent default config.
        """
        if self._cfg_path is None:
            return None
        try:
            return self._cfg_path.read_bytes()
        except OSError:
            return None

    def _reload_config_if_changed(self) -> None:
        """Swap in a fresh config if the watched file changed (FR-11).

        Runs only on the worker thread. A malformed file is logged once and the
        running config kept; timeout / level changes take effect on the next
        tick with no COM call, a device_id change retargets the backend in place.
        """
        if self._cfg_path is None:
            return
        raw = self._read_config()
        if raw == self._cfg_raw:
            return
        self._cfg_raw = raw
        if raw is None:  # file vanished - keep running with what we have
            return
        try:
            # Parsing already-read bytes and validating a dataclass replace()
            # can only ever raise ValueError (tomllib.TOMLDecodeError is one) -
            # no file I/O happens here, that's all in _read_config() above.
            new_cfg = config.load_bytes(raw)
            if self._overrides:
                new_cfg = replace(new_cfg, **self._overrides).validated()
        except ValueError as exc:
            msg = str(exc)
            if msg != self._reload_error:
                log.warning("config reload failed, keeping current settings: %s", msg)
                self._reload_error = msg
            return
        if self._reload_error is not None:
            log.info("config file parses again")
            self._reload_error = None
        with self._cfg_lock:
            old, self._cfg = self._cfg, new_cfg
        if new_cfg == old:
            return
        if new_cfg.device_id != old.device_id:
            # Worker owns the backend. Forget the tracked state so the next
            # tick re-issues set_level against the new device rather than
            # waiting for the next idle transition.
            self._backlight.device_id = new_cfg.device_id
            self._is_on = None
        log.info(
            "config reloaded: timeout=%.1fs on_level=%d autostart=%s",
            new_cfg.timeout, new_cfg.on_level, new_cfg.autostart,
        )
        if new_cfg.autostart != old.autostart and self._on_autostart_change is not None:
            try:
                self._on_autostart_change(new_cfg.autostart)
            except Exception:  # noqa: BLE001 - a scheduler hiccup must not kill the worker
                log.exception("autostart reconcile from reload failed")

    def _apply(self, on: bool, cfg: config.Config | None = None) -> None:
        # Called only from the worker thread (tick / the _run shutdown), so no
        # lock is needed and every backlight call keeps its apartment affinity.
        # `cfg`, when given, is the config tick() already fetched (one lock
        # acquire per tick instead of one here plus one in tick()'s own read).
        if on and self._paused:
            # Never assert the backlight on while paused (FR-8). A tick that
            # read `_paused` as False and was then preempted by pause() can
            # still land here with on=True.
            return
        if on == self._is_on:
            return
        now = time.monotonic()
        if self._last_error is not None and now - self._last_attempt < FAIL_RETRY_INTERVAL:
            return  # backing off from a persistent failure - don't hammer WMI every tick
        self._last_attempt = now
        level = (cfg if cfg is not None else self.config).on_level if on else LEVEL_OFF
        try:
            self._backlight.set_level(level)
        except Exception as exc:  # noqa: BLE001
            # NFR-4: no backlight fault may desync our model or kill the worker
            # thread. Leave _is_on unchanged, retry after the backoff, and log a
            # repeating failure only once.
            msg = str(exc)
            if msg != self._last_error:
                log.error("%s", msg)
                log.error("backlight control is failing; retrying every %.0fs", FAIL_RETRY_INTERVAL)
                self._last_error = msg
            return
        if self._last_error is not None:
            log.info("backlight control recovered")
            self._last_error = None
        log.info("backlight %s (level %d)", "ON" if on else "off", level)
        self._is_on = on

    def tick(self) -> None:
        """Evaluate the desired state once against the last keystroke time."""
        self._tick_count += 1
        if self._tick_count % CONFIG_POLL_TICKS == 0:
            self._reload_config_if_changed()
        if self._paused:
            # FR-8: hold the backlight off, ignore keystrokes. pause() only set
            # the flag; this tick (on the worker thread) is what actually turns
            # the light off, and keeps re-asserting off every 50 ms so a WMI
            # back-off that was mid-flight at pause() is still retried.
            # _apply is a no-op once _is_on is False.
            self._apply(on=False)
            return
        cfg = self.config
        idle_for = time.monotonic() - self._hook.last_key
        self._apply(on=idle_for < cfg.timeout, cfg=cfg)

    @property
    def paused(self) -> bool:
        return self._paused

    def pause(self) -> None:
        """Stop reacting to keystrokes; the worker turns the backlight off (FR-8).

        Called from the message-pump thread (tray Pause). It only sets the flag
        — the worker's next tick (~50 ms) issues the actual backlight call, so
        no COM call ever runs off the worker thread.
        """
        self._paused = True

    def resume(self) -> None:
        """Resume normal idle tracking; the next tick re-evaluates."""
        self._paused = False

    def toggle_pause(self) -> bool:
        """Flip pause state. Returns the new value (True == now paused)."""
        if self._paused:
            self.resume()
        else:
            self.pause()
        return self._paused

    def _run(self) -> None:
        # Every backlight/COM call happens on this one thread. The first tick
        # sees last_key == 0.0, so it immediately drives the backlight off
        # (FR-1); the last one restores the off state before we exit (FR-6).
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(POLL_INTERVAL)
        self._apply(on=False)

    def start(self) -> None:
        self._hook.install()          # must happen on the message-pump thread
        log.info("keyboard hook installed")
        self._worker.start()

    def stop(self, join_timeout: float = 2.0) -> None:
        self._stop.set()
        self._worker.join(timeout=join_timeout)
        if self._worker.is_alive():
            # The final _apply(on=False) in _run may not have completed (a slow
            # WMI call); the backlight could still be on. Hook removal below is
            # unconditional and still correct.
            log.warning("worker did not stop within 2s; backlight may still be lit")
        self._hook.uninstall()        # FR-6: hand keyboard control back to the system
        log.info("stopped, keyboard hook removed")


def _setup_logging(debug: bool, *, to_file: bool = True) -> None:
    level = logging.DEBUG if debug else logging.INFO
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(level)

    if sys.stderr is not None:  # None in a --noconsole (windowed) build
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        root.addHandler(sh)

    if not to_file:
        # An unelevated parent about to relaunch itself must not open the
        # rotating log file the elevated child will own (a rollover from two
        # processes races on Windows).
        return

    logfile = config.default_config_path().parent / "asus-kbd-backlight.log"
    try:
        logfile.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            logfile, maxBytes=512_000, backupCount=2, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)
        log.info("log file: %s", logfile)
    except OSError as exc:
        log.warning("no log file (%s)", exc)


def _install_console_ctrl_handler(thread_id: int) -> object | None:
    """Post WM_QUIT to the message-pump thread on Ctrl+C / Ctrl+Break / close.

    ``GetMessage`` blocks the main thread, so Python's own SIGINT handling never
    runs; a console control handler is the reliable way to break out cleanly.
    Returns the callback, which the caller must keep referenced.
    """
    if _kernel32 is None or _user32 is None:
        return None

    handler_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)

    def _handler(ctrl_type: int) -> int:
        log.info("console signal %d, shutting down", ctrl_type)
        _user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
        return 1  # handled

    cb = handler_type(_handler)
    if not _kernel32.SetConsoleCtrlHandler(cb, True):
        log.debug("SetConsoleCtrlHandler failed (no console?)")
    return cb


def _launch_settings(config_path: Path | None) -> None:
    """Open the settings window as a separate, unelevated process (NFR-5).

    Spawned on a throwaway thread: borrowing the shell token and creating the
    process can take a beat, and this is called from the message-pump thread
    that also services the low-level keyboard hook (NFR-6) - blocking it risks
    ``LowLevelHooksTimeout`` dropping keystrokes.
    """
    from asus_kbd_backlight import elevate

    def _spawn() -> None:
        log.info("opening the settings window")
        try:
            elevate.spawn_settings(config_path)
        except Exception:  # noqa: BLE001 - a failed spawn must not be silent nor fatal
            log.exception("could not open the settings window")

    threading.Thread(target=_spawn, name="settings-spawn", daemon=True).start()


def _install_tray(controller: Controller, config_path: Path | None = None):
    """Create the tray icon on the current (message-pump) thread.

    Returns the ``TrayIcon`` or ``None``; a failure to create it is logged and
    the daemon carries on headless (NFR-7).
    """
    if sys.platform != "win32":
        return None
    # NFR-7: nothing here may take the daemon down. The import, the ctypes
    # symbol wiring at module load, the TrayIcon build and install() all funnel
    # into "log it and run headless" - main() has already installed the hook
    # and started the worker by this point.
    try:
        from asus_kbd_backlight.tray import TrayIcon

        def on_toggle_pause() -> None:
            paused = controller.toggle_pause()
            tray.set_paused(paused)
            log.info("backlight %s from tray", "paused" if paused else "resumed")

        def status() -> str:
            state = "paused" if controller.paused else "running"
            return (
                f"asus-kbd-backlight {__version__} — {state}, "
                f"off after {controller.config.timeout:g}s"
            )

        tray = TrayIcon(
            on_settings=lambda: _launch_settings(config_path),
            on_toggle_pause=on_toggle_pause,
            on_quit=lambda: log.info("quit requested from tray"),
            tooltip_running=f"asus-kbd-backlight {__version__} — running",
            tooltip_paused=f"asus-kbd-backlight {__version__} — paused",
            status_provider=status,
        )
        tray.install()
    except Exception as exc:  # noqa: BLE001 - headless is always an acceptable fallback
        log.warning("could not create tray icon (%s); running headless", exc)
        return None
    return tray


def _make_autostart_reconciler(autostart_module) -> Callable[[bool], None]:
    """Build the ``on_autostart_change`` callback: off-thread and race-proof.

    Every live reload that flips ``autostart`` must reconcile off the worker's
    50 ms tick (`Schedule.Service` COM + an on-disk task write can block for
    tens of ms). A bare ``threading.Thread(...).start()`` per change is not
    enough: two toggles landing within one poll window (the settings window
    saved twice in quick succession) would run concurrently, and the one that
    happens to *finish* last would win rather than the one that was
    *commanded* last. The lock plus sequence number below fix that: a thread
    that loses the race for the lock checks, once it gets in, whether a newer
    request has already superseded it and no-ops if so - so the task always
    converges on the most recently requested state, never a stale one.

    ``on_autostart_change`` is invoked once from `main()` (the pump thread, for
    the startup reconcile - before the worker thread exists) and then only
    from the worker thread (`_reload_config_if_changed`) from then on, so the
    sequence counter never has two concurrent writers and needs no lock of its
    own; the lock below only serialises the spawned reconcile threads against
    each other.
    """
    lock = threading.Lock()
    state: dict[str, object] = {"seq": 0, "enabled": None}

    def on_autostart_change(enabled: bool) -> None:
        state["seq"] = int(state["seq"]) + 1
        seq = state["seq"]
        state["enabled"] = enabled

        def _run() -> None:
            with lock:
                if state["seq"] != seq:
                    return  # a newer toggle already superseded this one
                autostart_module.reconcile(state["enabled"])

        threading.Thread(target=_run, name="autostart-reconcile", daemon=True).start()

    return on_autostart_change


def _is_one_shot(args: argparse.Namespace) -> bool:
    """True for a CLI invocation that runs once and exits, never becoming the
    long-running daemon: ``--settings``, ``--set``, ``--install-task``,
    ``--uninstall-task``. Each of these ``return``s from ``main()`` before the
    daemon loop is ever reached, so none of them may attach to the daemon's
    rotating log file - a second handler racing the running daemon's own would
    make a size-boundary rollover fail on Windows.
    """
    return (
        args.settings
        or args.set is not None
        or args.install_task
        or args.uninstall_task
    )


def _needs_admin_in_place(args: argparse.Namespace) -> str | None:
    """The flag of a one-shot command that needs Administrator rights but runs
    in place (``should_elevate`` never re-launches it, so its output and exit
    code reach the caller) - or ``None``. One place, so a new such command
    can't forget the up-front check."""
    if args.install_task:
        return "--install-task"
    if args.uninstall_task:
        return "--uninstall-task"
    if args.set is not None and not args.dry_run:
        return "--set"
    return None


def _manages_autostart(args: argparse.Namespace, *, admin: bool = True) -> bool:
    """Whether this daemon run owns the Task Scheduler entry (FR-12).

    Only for a real, default-location, **elevated** run: never in
    ``--dry-run``, never off Windows, never with an explicit ``--config`` (the
    scheduled task has no ``--config``, so it would launch the daemon against a
    *different* file at logon - and a one-off ``--config`` test run must not
    leave a task or a freshly-written file behind), and never unelevated (a
    ``--no-elevate`` dev run reaching here without admin rights would just have
    every ``Schedule.Service`` call fail - autostart.py's own contract is that
    the *elevated* daemon owns the task).
    """
    return (
        admin
        and not args.dry_run
        and sys.platform == "win32"
        and args.config is None
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="asus-kbd-backlight", description=__doc__)
    p.add_argument("--config", type=Path, default=None, help="path to config.toml")
    p.add_argument("--timeout", type=float, default=None, help="override idle timeout (s)")
    p.add_argument(
        "--on-level", type=int, choices=(1, 2, 3), default=None, help="override brightness"
    )
    p.add_argument("--dry-run", action="store_true", help="run the logic without touching hardware")
    p.add_argument(
        "--settings", action="store_true",
        help="open the settings window instead of the daemon (never elevated)",
    )
    p.add_argument(
        "--no-elevate", action="store_true",
        help="do not re-launch elevated; run as-is (set on the elevated child)",
    )
    p.add_argument("--debug", action="store_true", help="verbose logging")
    p.add_argument(
        "--set", type=int, choices=(0, 1, 2, 3), default=None, metavar="LEVEL",
        help="set backlight to LEVEL and exit (probe device_id / level mapping)",
    )
    task = p.add_mutually_exclusive_group()
    task.add_argument(
        "--install-task", action="store_true",
        help="register the Task Scheduler autostart entry and exit",
    )
    task.add_argument(
        "--uninstall-task", action="store_true",
        help="remove the Task Scheduler autostart entry and exit",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = _parse_args(argv)
    if args.config is not None:
        # Pin a relative --config to *this* process's working directory. The
        # settings window is started through a shortcut whose working directory
        # is the exe's folder, so it must be handed an absolute path.
        args.config = args.config.resolve()

    # FR-7: decide elevation before configuring logging, so the unelevated
    # parent never opens the rotating log file. The child is re-launched with
    # the same argv plus --no-elevate, so this happens at most once.
    admin = is_admin()
    relaunching = should_elevate(args, admin=admin)
    # Short-lived sub-commands and the about-to-relaunch parent must NOT attach
    # to the daemon's rotating log file: a rollover racing between two
    # processes fails on Windows.
    _setup_logging(args.debug, to_file=not relaunching and not _is_one_shot(args))
    if relaunching:
        return relaunch_elevated(argv)

    if args.settings:
        from asus_kbd_backlight import app_settings

        return app_settings.run(args.config)

    needs_admin = _needs_admin_in_place(args)
    if needs_admin and not admin:
        # These run in place (never self-elevate, so their exit code and output
        # reach the caller) but need admin: say so up front instead of failing
        # later inside Task Scheduler COM or WMI.
        log.error("%s needs Administrator rights: run it from an elevated prompt", needs_admin)
        return 1

    if args.install_task or args.uninstall_task:
        from asus_kbd_backlight import autostart

        ok = autostart.reconcile(enabled=args.install_task)
        return 0 if ok else 1

    cfg = config.load(args.config)
    overrides: dict[str, object] = {}
    if args.timeout is not None:
        overrides["timeout"] = args.timeout
    if args.on_level is not None:
        overrides["on_level"] = args.on_level
    if overrides:
        cfg = replace(cfg, **overrides).validated()

    log.info(
        "asus-kbd-backlight %s | timeout=%.1fs on_level=%d device_id=%#010x%s",
        __version__, cfg.timeout, cfg.on_level, cfg.device_id,
        "  [DRY RUN]" if args.dry_run else "",
    )
    if not args.dry_run and not admin:
        log.warning("not running as Administrator - backlight control will be denied (see README)")

    if args.set is not None:
        backlight = get_backlight(cfg.device_id, dry_run=args.dry_run)
        try:
            backlight.set_level(args.set)
        except BacklightError as exc:
            log.error("%s", exc)
            return 1
        log.info("set level %d - done", args.set)
        return 0

    # Always watch the file the config came from (FR-11: it is the source of
    # truth). Any --timeout / --on-level override is re-applied on top of each
    # reload, so a hand-edit to the other keys still takes effect while an
    # explicit override is never undone.
    watch_path = args.config or config.default_config_path()

    on_autostart_change = None
    if _manages_autostart(args, admin=admin):
        from asus_kbd_backlight import autostart

        if not watch_path.exists():
            # First run: write the file so the "Start with Windows" checkbox
            # has a visible state, then register the task once (silently -
            # PRD open question 10).
            try:
                config.save(config.load(None), watch_path)
            except OSError as exc:
                log.warning("could not write %s: %s", watch_path, exc)
        on_autostart_change = _make_autostart_reconciler(autostart)
        # Off-thread from the very first reconcile too: Schedule.Service COM
        # can block for tens of ms, sometimes much longer under load, and this
        # is literally the process the registered logon trigger launches -
        # FR-1 (forced off at startup) and hook installation below must not
        # wait on it. Routed through the same reconciler as the live-reload
        # path so the two can never race each other into a stale end state.
        on_autostart_change(cfg.autostart)

    controller = Controller(
        cfg, dry_run=args.dry_run, config_path=watch_path, overrides=overrides,
        on_autostart_change=on_autostart_change,
    )
    _ctrl_cb = None
    try:
        controller.start()
    except OSError as exc:
        log.error("could not start: %s", exc)
        return 1

    if _kernel32 is not None:
        _ctrl_cb = _install_console_ctrl_handler(_kernel32.GetCurrentThreadId())

    tray = _install_tray(controller, args.config)  # same thread as pump_messages() (NFR-6)

    log.info("running - type to light the keyboard; Ctrl+C or close this window to quit")
    try:
        pump_messages()  # blocks until WM_QUIT
    except KeyboardInterrupt:
        pass
    finally:
        if tray is not None:
            tray.remove()
        controller.stop()
    del _ctrl_cb
    return 0

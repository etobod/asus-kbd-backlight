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
from pathlib import Path

from asus_kbd_backlight import __version__, config
from asus_kbd_backlight.backlight import LEVEL_OFF, BacklightError, get_backlight
from asus_kbd_backlight.hook import KeyboardIdleHook, pump_messages

POLL_INTERVAL = 0.05  # 50 ms -> well inside the <500 ms turn-off target
WM_QUIT = 0x0012

log = logging.getLogger("asus_kbd_backlight")

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if sys.platform == "win32" else None
_user32 = ctypes.WinDLL("user32", use_last_error=True) if sys.platform == "win32" else None


class Controller:
    def __init__(self, cfg: config.Config, *, dry_run: bool = False) -> None:
        self._cfg = cfg
        self._backlight = get_backlight(cfg.device_id, dry_run=dry_run)
        self._hook = KeyboardIdleHook()
        self._is_on: bool | None = None
        self._last_error: str | None = None
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._run, name="backlight-worker", daemon=True)

    def _apply(self, on: bool) -> None:
        if on == self._is_on:
            return
        level = self._cfg.on_level if on else LEVEL_OFF
        try:
            self._backlight.set_level(level)
        except BacklightError as exc:
            # NFR-4: a hardware failure must not desync our model or kill the
            # process without cleanup. Leave _is_on unchanged and retry next tick,
            # but log a repeating failure only once.
            msg = str(exc)
            if msg != self._last_error:
                log.error("%s", msg)
                log.error("backlight control is failing; will keep retrying quietly")
                self._last_error = msg
            return
        if self._last_error is not None:
            log.info("backlight control recovered")
            self._last_error = None
        log.info("backlight %s (level %d)", "ON" if on else "off", level)
        self._is_on = on

    def tick(self) -> None:
        """Evaluate the desired state once against the last keystroke time."""
        idle_for = time.monotonic() - self._hook.last_key
        self._apply(on=idle_for < self._cfg.timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(POLL_INTERVAL)

    def start(self) -> None:
        self._hook.install()          # must happen on the message-pump thread
        log.info("keyboard hook installed")
        self._apply(on=False)          # FR-1: known-off at startup
        self._worker.start()

    def stop(self) -> None:
        self._stop.set()
        self._worker.join(timeout=1.0)
        self._apply(on=False)
        self._hook.uninstall()        # FR-6: hand keyboard control back to the system
        log.info("stopped, keyboard hook removed")


def _setup_logging(debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(level)

    if sys.stderr is not None:  # None in a --noconsole (windowed) build
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        root.addHandler(sh)

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


def _is_admin() -> bool:
    if _kernel32 is None:
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="asus-kbd-backlight", description=__doc__)
    p.add_argument("--config", type=Path, default=None, help="path to config.toml")
    p.add_argument("--timeout", type=float, default=None, help="override idle timeout (s)")
    p.add_argument(
        "--on-level", type=int, choices=(1, 2, 3), default=None, help="override brightness"
    )
    p.add_argument("--dry-run", action="store_true", help="run the logic without touching hardware")
    p.add_argument("--debug", action="store_true", help="verbose logging")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _setup_logging(args.debug)

    cfg = config.load(args.config)
    if args.timeout is not None:
        cfg = config.Config(args.timeout, cfg.on_level, cfg.device_id).validated()
    if args.on_level is not None:
        cfg = config.Config(cfg.timeout, args.on_level, cfg.device_id).validated()

    log.info(
        "asus-kbd-backlight %s | timeout=%.1fs on_level=%d device_id=%#010x%s",
        __version__, cfg.timeout, cfg.on_level, cfg.device_id,
        "  [DRY RUN]" if args.dry_run else "",
    )
    if not args.dry_run and not _is_admin():
        log.warning("not running as Administrator - backlight control will be denied (see README)")

    controller = Controller(cfg, dry_run=args.dry_run)
    _ctrl_cb = None
    try:
        controller.start()
    except OSError as exc:
        log.error("could not start: %s", exc)
        return 1

    if _kernel32 is not None:
        _ctrl_cb = _install_console_ctrl_handler(_kernel32.GetCurrentThreadId())

    log.info("running - type to light the keyboard; Ctrl+C or close this window to quit")
    try:
        pump_messages()  # blocks until WM_QUIT
    except KeyboardInterrupt:
        pass
    finally:
        controller.stop()
    del _ctrl_cb
    return 0

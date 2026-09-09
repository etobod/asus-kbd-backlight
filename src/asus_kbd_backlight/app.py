"""Wiring: keyboard-idle hook -> state machine -> backlight.

The hook thread does no work beyond stamping a timestamp (NFR-1). A separate
worker thread compares that timestamp against the timeout and issues a control
call *only* when the desired state changes (NFR-2).
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

from asus_kbd_backlight import __version__, config
from asus_kbd_backlight.backlight import LEVEL_OFF, BacklightError, get_backlight
from asus_kbd_backlight.hook import KeyboardIdleHook, pump_messages

POLL_INTERVAL = 0.05  # 50 ms -> well inside the <500 ms turn-off target


class Controller:
    def __init__(self, cfg: config.Config, *, dry_run: bool = False) -> None:
        self._cfg = cfg
        self._backlight = get_backlight(cfg.device_id, dry_run=dry_run)
        self._hook = KeyboardIdleHook()
        self._is_on: bool | None = None
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
            # process without cleanup. Leave _is_on unchanged and retry next tick.
            print(f"asus-kbd-backlight: {exc}", file=sys.stderr)
            return
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
        self._apply(on=False)          # FR-1: known-off at startup
        self._worker.start()

    def stop(self) -> None:
        self._stop.set()
        self._worker.join(timeout=1.0)
        self._apply(on=False)
        self._hook.uninstall()        # FR-6: hand keyboard control back to the system


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="asus-kbd-backlight", description=__doc__)
    p.add_argument("--config", type=Path, default=None, help="path to config.toml")
    p.add_argument("--timeout", type=float, default=None, help="override idle timeout (s)")
    p.add_argument("--on-level", type=int, choices=(1, 2, 3), default=None, help="override brightness")
    p.add_argument("--dry-run", action="store_true", help="run the logic without touching hardware")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    cfg = config.load(args.config)
    if args.timeout is not None:
        cfg = config.Config(args.timeout, cfg.on_level, cfg.device_id).validated()
    if args.on_level is not None:
        cfg = config.Config(cfg.timeout, args.on_level, cfg.device_id).validated()

    controller = Controller(cfg, dry_run=args.dry_run)
    controller.start()
    try:
        pump_messages()  # blocks until WM_QUIT
    except KeyboardInterrupt:
        pass
    finally:
        controller.stop()
    return 0

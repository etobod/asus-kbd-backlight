"""Configuration loading.

Real configuration lives outside the source tree, in a TOML file at
``%APPDATA%\\asus-kbd-backlight\\config.toml``. Every key is optional; missing
keys fall back to the defaults below (FR-4).
"""

from __future__ import annotations

import logging
import math
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib

_log = logging.getLogger(__name__)

_KNOWN_KEYS = {"timeout", "on_level", "device_id", "autostart"}

# ASUS ACPI endpoint for keyboard backlight brightness (AsusAtkWmi_WMNB / DEVS).
DEFAULT_DEVICE_ID = 0x00050021

# Seconds of keyboard idleness before the backlight turns off.
DEFAULT_TIMEOUT = 3.0

# Brightness while typing: 1 = 33%, 2 = 66%, 3 = 100%. 0 is "off".
DEFAULT_ON_LEVEL = 1

# Register a Task Scheduler logon entry so the daemon starts elevated at logon
# (FR-12). On by default; the elevated daemon reconciles the actual task.
DEFAULT_AUTOSTART = True


@dataclass(frozen=True)
class Config:
    timeout: float = DEFAULT_TIMEOUT
    on_level: int = DEFAULT_ON_LEVEL
    device_id: int = DEFAULT_DEVICE_ID
    autostart: bool = DEFAULT_AUTOSTART

    def validated(self) -> Config:
        # TOML has inf and nan literals (and 1e999 parses to inf). An infinite
        # timeout would never turn the light off; nan compares False with
        # everything, so the daemon would never turn it *on*.
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError(f"timeout must be a finite number > 0, got {self.timeout!r}")
        if self.on_level not in (1, 2, 3):
            raise ValueError(f"on_level must be 1, 2 or 3, got {self.on_level!r}")
        if not (0 < self.device_id <= 0xFFFFFFFF):
            raise ValueError(f"device_id out of range: {self.device_id!r}")
        if not isinstance(self.autostart, bool):
            raise ValueError(f"autostart must be true or false, got {self.autostart!r}")
        return self


def default_config_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "asus-kbd-backlight" / "config.toml"


def _coerce_device_id(raw: object) -> int:
    if isinstance(raw, bool):  # bool is an int subclass; never a device id
        raise ValueError(f"device_id must be an int or hex string, got {raw!r}")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        s = raw.strip()
        try:
            return int(s, 0)  # "0x00050021", or plain decimal "327713"
        except ValueError:
            pass
        try:
            return int(s, 16)  # bare hex without 0x, e.g. "00050021"
        except ValueError:
            raise ValueError(f"device_id is not a valid integer or hex string: {raw!r}") from None
    raise ValueError(f"device_id must be an int or hex string, got {raw!r}")


def load(path: Path | None = None) -> Config:
    """Load configuration from ``path`` (or the default location).

    A missing file is not an error: it yields the default configuration.
    """
    path = path or default_config_path()
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return Config().validated()
    return load_bytes(raw)


def load_bytes(raw: bytes) -> Config:
    """Parse and validate a config from raw TOML bytes.

    Split out from :func:`load` so a caller that has already read the file (the
    live-reload watcher, which hashes the bytes) can parse the *same* bytes
    rather than re-opening the path and racing a delete-and-recreate editor
    into a silent default config.
    """
    data = tomllib.loads(raw.decode("utf-8"))

    unknown = sorted(set(data) - _KNOWN_KEYS)
    if unknown:
        _log.warning("ignoring unknown config key(s): %s", ", ".join(unknown))

    raw_timeout = data.get("timeout", DEFAULT_TIMEOUT)
    try:
        timeout = float(raw_timeout)
    except (TypeError, ValueError):
        raise ValueError(f"config: 'timeout' must be a number, got {raw_timeout!r}") from None
    except OverflowError:
        # A TOML integer too big for a float (hundreds of digits). Left as an
        # OverflowError it would slip past every ValueError handler - the live
        # reload's among them, killing the worker thread.
        raise ValueError("config: 'timeout' is far too large") from None

    raw_level = data.get("on_level", DEFAULT_ON_LEVEL)
    if isinstance(raw_level, bool) or not isinstance(raw_level, int):
        raise ValueError(f"config: 'on_level' must be an integer 1-3, got {raw_level!r}")

    raw_autostart = data.get("autostart", DEFAULT_AUTOSTART)
    if not isinstance(raw_autostart, bool):
        raise ValueError(f"config: 'autostart' must be true or false, got {raw_autostart!r}")

    return Config(
        timeout=timeout,
        on_level=raw_level,
        device_id=_coerce_device_id(data.get("device_id", DEFAULT_DEVICE_ID)),
        autostart=raw_autostart,
    ).validated()


def save(cfg: Config, path: Path | None = None) -> Path:
    """Write ``cfg`` to ``path`` (or the default location) atomically.

    The file is the single source of truth (FR-11): the settings window calls
    this, then exits, and the running daemon picks the change up on its next
    poll. Written to a sibling ``*.tmp`` and ``os.replace``-d into place so a
    concurrent reader never sees a half-written file. Returns the path written.

    Serialised by hand rather than via a TOML-writer dependency: four scalar
    keys keep the frozen bundle one library lighter.
    """
    cfg.validated()
    path = path or default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # validated() above already did `0 < device_id <= 0xFFFFFFFF`, a numeric
    # comparison that raises before here if device_id isn't already an int.
    device_id = cfg.device_id
    body = (
        "# Written by asus-kbd-backlight. Hand-edits are picked up live (FR-11),\n"
        "# but saving from the settings window rewrites this file and does not\n"
        "# keep comments or unrecognised keys.\n"
        f"timeout = {cfg.timeout!r}\n"
        f"on_level = {cfg.on_level}\n"
        f"autostart = {str(cfg.autostart).lower()}\n"
        f'device_id = "{device_id:#010x}"\n'
    )

    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=path.name + ".", suffix=".tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return path

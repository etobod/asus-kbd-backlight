"""Configuration loading.

Real configuration lives outside the source tree, in a TOML file at
``%APPDATA%\\asus-kbd-backlight\\config.toml``. Every key is optional; missing
keys fall back to the defaults below (FR-4).
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib

_log = logging.getLogger(__name__)

_KNOWN_KEYS = {"timeout", "on_level", "device_id"}

# ASUS ACPI endpoint for keyboard backlight brightness (AsusAtkWmi_WMNB / DEVS).
DEFAULT_DEVICE_ID = 0x00050021

# Seconds of keyboard idleness before the backlight turns off.
DEFAULT_TIMEOUT = 3.0

# Brightness while typing: 1 = 33%, 2 = 66%, 3 = 100%. 0 is "off".
DEFAULT_ON_LEVEL = 1


@dataclass(frozen=True)
class Config:
    timeout: float = DEFAULT_TIMEOUT
    on_level: int = DEFAULT_ON_LEVEL
    device_id: int = DEFAULT_DEVICE_ID

    def validated(self) -> Config:
        if self.timeout <= 0:
            raise ValueError(f"timeout must be > 0, got {self.timeout!r}")
        if self.on_level not in (1, 2, 3):
            raise ValueError(f"on_level must be 1, 2 or 3, got {self.on_level!r}")
        if not (0 < self.device_id <= 0xFFFFFFFF):
            raise ValueError(f"device_id out of range: {self.device_id!r}")
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
    if not path.is_file():
        return Config().validated()

    with path.open("rb") as fh:
        data = tomllib.load(fh)

    unknown = sorted(set(data) - _KNOWN_KEYS)
    if unknown:
        _log.warning("ignoring unknown config key(s): %s", ", ".join(unknown))

    raw_timeout = data.get("timeout", DEFAULT_TIMEOUT)
    try:
        timeout = float(raw_timeout)
    except (TypeError, ValueError):
        raise ValueError(f"config: 'timeout' must be a number, got {raw_timeout!r}") from None

    raw_level = data.get("on_level", DEFAULT_ON_LEVEL)
    if isinstance(raw_level, bool) or not isinstance(raw_level, int):
        raise ValueError(f"config: 'on_level' must be an integer 1-3, got {raw_level!r}")

    return Config(
        timeout=timeout,
        on_level=raw_level,
        device_id=_coerce_device_id(data.get("device_id", DEFAULT_DEVICE_ID)),
    ).validated()

"""ASUS keyboard backlight control via the ACPI/WMI interface.

Brightness is written through the ``AsusAtkWmi_WMNB`` class, method ``DEVS``,
targeting ``Device_ID = 0x00050021`` by default. State is never read back
(``DSTS`` is unreliable across firmware); callers track it internally (FR-1,
R-3).
"""

from __future__ import annotations

import contextlib
import logging
from typing import Protocol

_log = logging.getLogger(__name__)

WMI_MONIKER = r"winmgmts:root\WMI"
WMI_CLASS = "AsusAtkWmi_WMNB"

LEVEL_OFF = 0
LEVEL_MIN = 1  # 33%
LEVEL_MID = 2  # 66%
LEVEL_MAX = 3  # 100%
_VALID_LEVELS = (LEVEL_OFF, LEVEL_MIN, LEVEL_MID, LEVEL_MAX)


class BacklightError(RuntimeError):
    """A hardware communication failure while setting brightness (NFR-4)."""


class Backlight(Protocol):
    def set_level(self, level: int) -> None: ...


class WmiBacklight:
    """Talks to ATKACPI through WMI. Requires Windows, pywin32 and elevation.

    The COM objects are created lazily on the thread that first calls
    :meth:`set_level` (the controller's worker thread), which is also where
    ``CoInitialize`` must run.
    """

    def __init__(self, device_id: int = 0x00050021) -> None:
        self.device_id = device_id
        self._instance = None

    def _bind(self):
        if self._instance is not None:
            return self._instance
        try:
            import pythoncom
            import win32com.client
        except ImportError as exc:  # pragma: no cover
            raise BacklightError("pywin32 is required for backlight control") from exc

        with contextlib.suppress(Exception):  # harmless if already done on this thread
            pythoncom.CoInitialize()

        try:
            service = win32com.client.GetObject(WMI_MONIKER)
            rows = list(service.ExecQuery(f"SELECT * FROM {WMI_CLASS}"))
        except Exception as exc:  # noqa: BLE001
            hint = ""
            if "denied" in str(exc).lower():
                hint = " - run this program as Administrator"
            raise BacklightError(f"cannot reach {WMI_CLASS}: {exc}{hint}") from exc

        if not rows:
            raise BacklightError(
                f"{WMI_CLASS} exposes no instances - is the ASUS System Control "
                "Interface driver installed?"
            )
        _log.debug("bound %s (%d instance(s))", WMI_CLASS, len(rows))
        self._instance = rows[0]
        return self._instance

    def set_level(self, level: int) -> None:
        if level not in _VALID_LEVELS:
            raise ValueError(f"level must be 0-3, got {level!r}")
        instance = self._bind()
        try:
            result = instance.DEVS(self.device_id, level)
        except Exception as exc:  # noqa: BLE001
            raise BacklightError(
                f"DEVS({self.device_id:#010x}, {level}) failed: {exc}"
            ) from exc
        _log.info("DEVS(%#010x, %d) -> %r", self.device_id, level, result)


class NullBacklight:
    """No-op backend for dry runs and for importing on non-Windows hosts."""

    def __init__(self, device_id: int = 0x00050021) -> None:
        self.device_id = device_id
        self.last_level: int | None = None

    def set_level(self, level: int) -> None:
        if level not in _VALID_LEVELS:
            raise ValueError(f"level must be 0-3, got {level!r}")
        self.last_level = level
        _log.info("[dry-run] would set level %d on %#010x", level, self.device_id)


def get_backlight(device_id: int, *, dry_run: bool = False) -> Backlight:
    import sys

    if dry_run or sys.platform != "win32":
        return NullBacklight(device_id)
    return WmiBacklight(device_id)

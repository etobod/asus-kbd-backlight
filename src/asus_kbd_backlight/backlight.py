"""ASUS keyboard backlight control via the ACPI/WMI interface.

Brightness is written through the ``AsusAtkWmi_WMNB`` class, method ``DEVS``
(``[in] uint32 Device_ID, [in] uint32 Control_status, [out] uint32 result``),
targeting ``Device_ID = 0x00050021`` by default. State is never read back
(``DSTS`` is unreliable across firmware); callers track it internally (FR-1,
R-3).

``DEVS`` is invoked with ``ExecMethod_`` **on the queried instance** (not the
class object, which WMI rejects) using an ``InParameters`` template taken from
the class. Elevation is required; without it every method call fails with
"Invalid method Parameter(s)".
"""

from __future__ import annotations

import contextlib
import logging
from typing import Protocol

_log = logging.getLogger(__name__)

WMI_NAMESPACE_MONIKER = r"winmgmts:\\.\root\WMI"
WMI_CLASS = "AsusAtkWmi_WMNB"

LEVEL_OFF = 0
LEVEL_MIN = 1  # 33%
LEVEL_MID = 2  # 66%
LEVEL_MAX = 3  # 100%
_VALID_LEVELS = (LEVEL_OFF, LEVEL_MIN, LEVEL_MID, LEVEL_MAX)

# Control_status must have bit 7 set for the change to actually engage the
# illumination; sending a bare 0..3 only writes the level register (a readback
# then reports the new level) while the backlight stays dark. This matches the
# Linux asus-wmi driver (ctrl_param = 0x80 | value) and G-Helper (brightness | 0x80).
_KBD_BACKLIGHT_SET = 0x80


class BacklightError(RuntimeError):
    """A hardware communication failure while setting brightness (NFR-4)."""


class Backlight(Protocol):
    def set_level(self, level: int) -> None: ...


def _co_initialize() -> None:
    import pythoncom

    # S_FALSE (already initialised on this thread) is fine; anything else raises.
    with contextlib.suppress(Exception):
        pythoncom.CoInitialize()


class WmiBacklight:
    """Talks to ATKACPI through WMI. Requires Windows, pywin32 and elevation.

    COM is touched only from the thread that calls :meth:`set_level` (the
    controller's worker thread); that thread must have called ``CoInitialize``,
    which :meth:`set_level` ensures. The bound objects are created on that same
    thread, so there is no cross-apartment marshalling.
    """

    def __init__(self, device_id: int = 0x00050021) -> None:
        self.device_id = device_id
        self._instance = None
        self._devs = None

    def _bind(self):
        if self._instance is not None:
            return self._instance, self._devs
        try:
            import win32com.client
        except ImportError as exc:  # pragma: no cover
            raise BacklightError("pywin32 is required for backlight control") from exc

        try:
            service = win32com.client.GetObject(WMI_NAMESPACE_MONIKER)
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
        self._instance = rows[0]
        self._devs = service.Get(WMI_CLASS).Methods_("DEVS")
        _log.debug("bound %s instance and DEVS method", WMI_CLASS)
        return self._instance, self._devs

    def set_level(self, level: int) -> None:
        if level not in _VALID_LEVELS:
            raise ValueError(f"level must be 0-3, got {level!r}")

        control_status = _KBD_BACKLIGHT_SET | level

        _co_initialize()
        instance, devs = self._bind()
        try:
            params = devs.InParameters.SpawnInstance_()
            params.Properties_.Item("Device_ID").Value = self.device_id
            params.Properties_.Item("Control_status").Value = control_status
            out = instance.ExecMethod_("DEVS", params)
            result = out.Properties_.Item("result").Value
        except Exception as exc:  # noqa: BLE001
            hint = ""
            if "parameter" in str(exc).lower():
                hint = " - run this program as Administrator"
            raise BacklightError(
                f"DEVS({self.device_id:#010x}, {control_status:#04x}) failed: {exc}{hint}"
            ) from exc
        _log.info(
            "DEVS(%#010x, %#04x) -> %s  [level %d]", self.device_id, control_status, result, level
        )


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

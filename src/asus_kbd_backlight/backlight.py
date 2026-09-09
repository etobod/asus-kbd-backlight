"""ASUS keyboard backlight control via the ACPI/WMI interface.

Brightness is written through the ``AsusAtkWmi_WMNB`` class, method ``DEVS``,
targeting ``Device_ID = 0x00050021`` by default. State is never read back
(``DSTS`` is unreliable across firmware); callers track it internally (FR-1,
R-3).
"""

from __future__ import annotations

from typing import Protocol

WMI_NAMESPACE = "root\\WMI"
WMI_CLASS = "AsusAtkWmi_WMNB"

LEVEL_OFF = 0
LEVEL_MIN = 1  # 33%
LEVEL_MID = 2  # 66%
LEVEL_MAX = 3  # 100%


class BacklightError(RuntimeError):
    """A hardware communication failure while setting brightness (NFR-4)."""


class Backlight(Protocol):
    def set_level(self, level: int) -> None: ...


class WmiBacklight:
    """Talks to ATKACPI through WMI. Requires Windows, pywin32 and elevation."""

    def __init__(self, device_id: int = 0x00050021) -> None:
        self.device_id = device_id
        self._method = None  # bound lazily so tests can import this module anywhere

    def _wmnb(self):
        if self._method is None:
            try:
                import win32com.client
            except ImportError as exc:  # pragma: no cover
                raise BacklightError(
                    "pywin32 is required for backlight control on Windows"
                ) from exc
            try:
                locator = win32com.client.Dispatch("WbemScripting.SWbemLocator")
                service = locator.ConnectServer(".", WMI_NAMESPACE)
                self._method = service.Get(WMI_CLASS).SpawnInstance_()
            except Exception as exc:  # noqa: BLE001 - surface any COM failure uniformly
                raise BacklightError(f"cannot reach {WMI_CLASS}: {exc}") from exc
        return self._method

    def set_level(self, level: int) -> None:
        if level not in (LEVEL_OFF, LEVEL_MIN, LEVEL_MID, LEVEL_MAX):
            raise ValueError(f"level must be 0-3, got {level!r}")
        instance = self._wmnb()
        try:
            instance.DEVS(self.device_id, level)
        except Exception as exc:  # noqa: BLE001
            raise BacklightError(f"DEVS({self.device_id:#010x}, {level}) failed: {exc}") from exc


class NullBacklight:
    """No-op backend for dry runs and for importing on non-Windows hosts."""

    def __init__(self, device_id: int = 0x00050021) -> None:
        self.device_id = device_id
        self.last_level: int | None = None

    def set_level(self, level: int) -> None:
        self.last_level = level


def get_backlight(device_id: int, *, dry_run: bool = False) -> Backlight:
    import sys

    if dry_run or sys.platform != "win32":
        return NullBacklight(device_id)
    return WmiBacklight(device_id)

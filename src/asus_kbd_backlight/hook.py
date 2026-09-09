"""System-wide low-level keyboard hook (``WH_KEYBOARD_LL``).

Privacy note (NFR-3): the callback records a monotonic timestamp and nothing
else. It never inspects the virtual key code, accumulates no data, writes
nothing to disk, and opens no network connection. This is the entire security
surface of the tool — the callback below is deliberately trivial so it can be
audited at a glance.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

WH_KEYBOARD_LL = 13
HC_ACTION = 0

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_HOOKPROC = ctypes.CFUNCTYPE(
    ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)

# Without an explicit restype ctypes assumes c_int (32-bit) and truncates the
# 64-bit handle these return, which yields an invalid hMod and makes
# SetWindowsHookExW fail with ERROR_MOD_NOT_FOUND (126).
_kernel32.GetModuleHandleW.restype = wintypes.HMODULE
_kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)

_user32.SetWindowsHookExW.restype = wintypes.HHOOK
_user32.SetWindowsHookExW.argtypes = (
    ctypes.c_int, _HOOKPROC, wintypes.HMODULE, wintypes.DWORD,
)
_user32.CallNextHookEx.restype = wintypes.LPARAM
_user32.CallNextHookEx.argtypes = (
    wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM,
)
_user32.UnhookWindowsHookEx.restype = wintypes.BOOL
_user32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)


class KeyboardIdleHook:
    """Installs a keyboard-only hook and exposes the time of the last keystroke.

    Pointer devices (mouse, touchpad) do not pass through ``WH_KEYBOARD_LL``,
    so ``last_key`` advances only on real key events (FR-2).
    """

    def __init__(self) -> None:
        self.last_key: float = 0.0
        self._handle: wintypes.HHOOK | None = None
        # Keep a reference so the trampoline is not garbage-collected.
        self._proc = _HOOKPROC(self._callback)

    def _callback(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code == HC_ACTION:
            self.last_key = time.monotonic()  # timestamp only — see module docstring
        return _user32.CallNextHookEx(None, n_code, w_param, l_param)

    def install(self) -> None:
        if self._handle is not None:
            return
        # WH_KEYBOARD_LL may be installed with a NULL module handle when the
        # callback lives in the current process (which a ctypes trampoline
        # does). Fall back to the real module handle for older systems that
        # still demand one.
        last_err = 0
        for module in (None, _kernel32.GetModuleHandleW(None)):
            handle = _user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, module, 0)
            if handle:
                self._handle = handle
                return
            last_err = ctypes.get_last_error()
        raise ctypes.WinError(last_err)

    def uninstall(self) -> None:
        if self._handle is None:
            return
        _user32.UnhookWindowsHookEx(self._handle)
        self._handle = None

    def __enter__(self) -> KeyboardIdleHook:
        self.install()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.uninstall()


def pump_messages() -> None:
    """Run the Windows message loop.

    A low-level hook is only serviced while the installing thread pumps
    messages, so the caller must run this on the same thread that called
    :meth:`KeyboardIdleHook.install`.
    """
    msg = wintypes.MSG()
    while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        _user32.TranslateMessage(ctypes.byref(msg))
        _user32.DispatchMessageW(ctypes.byref(msg))

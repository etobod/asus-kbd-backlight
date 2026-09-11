"""System-tray icon, context menu and pause/resume (FR-8).

The tray is a hidden top-level window that lives on the daemon's **one**
message-pump thread (NFR-6): Windows calls its ``WndProc`` from the same
``GetMessage``/``DispatchMessage`` loop that services the keyboard hook, so no
second message loop is introduced. It is a real (never-shown) top-level window
rather than a message-only one, because ``message-only`` windows do not receive
the broadcast ``TaskbarCreated`` message Explorer sends after a restart
(NFR-8).

Every Win32 call is a thin wrapper and the menu-id -> callback mapping is a
plain method (:meth:`TrayIcon._dispatch`), so the dispatch table, the tooltip
text and the Explorer-restart re-add are unit-testable without creating a
window.
"""

from __future__ import annotations

import ctypes
import logging
from collections.abc import Callable
from ctypes import wintypes

from asus_kbd_backlight import paths

log = logging.getLogger(__name__)

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_shell32 = ctypes.WinDLL("shell32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# --- window messages ---------------------------------------------------------
WM_NULL = 0x0000
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_QUERYENDSESSION = 0x0011
WM_COMMAND = 0x0111
WM_TIMER = 0x0113
WM_APP = 0x8000
WM_TRAYICON = WM_APP + 1  # our uCallbackMessage
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205

WS_OVERLAPPED = 0x00000000
ERROR_CLASS_ALREADY_EXISTS = 1410

# --- Shell_NotifyIcon ------------------------------------------------------
NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004

# --- menus ---------------------------------------------------------------
MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
TPM_RIGHTBUTTON = 0x0002

# --- LoadImage ---------------------------------------------------------------
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
LR_DEFAULTSIZE = 0x00000040

_TIP_MAX = 128

# Poll the status text on a WM_TIMER (pump thread, so Shell_NotifyIcon stays on
# the window's own thread) and push a NIM_MODIFY only when it actually changed -
# this is how a live config reload's new timeout reaches the tooltip (FR-11).
_TOOLTIP_TIMER_ID = 1
_TOOLTIP_TIMER_MS = 2000


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * _TIP_MAX),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
    ]


WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


# Handle-returning calls need an explicit restype or the 64-bit value is
# truncated to c_int (the trap that broke SetWindowsHookEx - see DESIGN).
_kernel32.GetModuleHandleW.restype = wintypes.HMODULE
_kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
_user32.RegisterClassW.restype = wintypes.ATOM
_user32.RegisterClassW.argtypes = (ctypes.POINTER(WNDCLASSW),)
_user32.CreateWindowExW.restype = wintypes.HWND
_user32.CreateWindowExW.argtypes = (
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
)
_user32.DefWindowProcW.restype = ctypes.c_ssize_t
_user32.DefWindowProcW.argtypes = (
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)
_user32.DestroyWindow.argtypes = (wintypes.HWND,)
_user32.DestroyIcon.restype = wintypes.BOOL
_user32.DestroyIcon.argtypes = (wintypes.HICON,)
_user32.PostMessageW.argtypes = (
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)
_user32.RegisterWindowMessageW.restype = wintypes.UINT
_user32.RegisterWindowMessageW.argtypes = (wintypes.LPCWSTR,)
_user32.CreatePopupMenu.restype = wintypes.HMENU
_user32.AppendMenuW.argtypes = (
    wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR
)
_user32.TrackPopupMenu.argtypes = (
    wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, wintypes.HWND, wintypes.LPVOID,
)
_user32.DestroyMenu.argtypes = (wintypes.HMENU,)
_user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
_user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
_user32.PostQuitMessage.argtypes = (ctypes.c_int,)
_user32.SetTimer.restype = ctypes.c_size_t
_user32.SetTimer.argtypes = (wintypes.HWND, ctypes.c_size_t, wintypes.UINT, ctypes.c_void_p)
_user32.KillTimer.restype = wintypes.BOOL
_user32.KillTimer.argtypes = (wintypes.HWND, ctypes.c_size_t)
_user32.LoadImageW.restype = wintypes.HANDLE
_user32.LoadImageW.argtypes = (
    wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
    ctypes.c_int, ctypes.c_int, wintypes.UINT,
)
_shell32.Shell_NotifyIconW.restype = wintypes.BOOL
_shell32.Shell_NotifyIconW.argtypes = (wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW))


class TrayIcon:
    """A tray icon whose three menu actions are injected as callbacks.

    ``on_settings``/``on_toggle_pause``/``on_quit`` take no arguments. They run
    on the message-pump thread; keep them quick and non-blocking (the daemon's
    keyboard hook is serviced on the same thread).
    """

    ID_SETTINGS = 1
    ID_PAUSE = 2
    ID_QUIT = 3

    _CLASS_NAME = "AsusKbdBacklightTray"
    _WINDOW_NAME = "asus-kbd-backlight"
    _ICON_UID = 1

    def __init__(
        self,
        *,
        on_settings: Callable[[], object],
        on_toggle_pause: Callable[[], object],
        on_quit: Callable[[], object],
        tooltip_running: str = "asus-kbd-backlight — running",
        tooltip_paused: str = "asus-kbd-backlight — paused",
        status_provider: Callable[[], str] | None = None,
    ) -> None:
        self._on_settings = on_settings
        self._on_toggle_pause = on_toggle_pause
        self._on_quit = on_quit
        self._tooltip_running = tooltip_running
        self._tooltip_paused = tooltip_paused
        # When set, this supplies the whole tooltip (version + state + current
        # timeout); a WM_TIMER polls it so a live config reload is reflected
        # without the daemon reaching into the tray from the worker thread.
        self._status_provider = status_provider
        self._paused = False
        self._last_tip: str | None = None
        self._hwnd: int | None = None
        self._hicon: int | None = None
        self._icon_paused_state: bool | None = None  # which state _hicon reflects
        self._proc = WNDPROC(self._wndproc)  # keep a ref so it is not GC'd
        # Explorer broadcasts this after a restart; we re-add the icon on it.
        self._taskbar_created = _user32.RegisterWindowMessageW("TaskbarCreated")

    # -- logic that does not need a window --------------------------------

    def tooltip(self) -> str:
        if self._status_provider is not None:
            try:
                return self._status_provider()
            except Exception:  # noqa: BLE001 - fall back, never crash the pump
                log.exception("tray: status_provider raised")
        return self._tooltip_paused if self._paused else self._tooltip_running

    def _dispatch(self, command_id: int) -> None:
        """Route a menu command id to its injected callback."""
        handler = {
            self.ID_SETTINGS: (self._on_settings, "settings"),
            self.ID_PAUSE: (self._on_toggle_pause, "pause/resume"),
            self.ID_QUIT: (self._quit, "quit"),
        }.get(command_id)
        if handler is None:
            log.debug("tray: ignoring unknown command id %d", command_id)
            return
        cb, what = handler
        self._invoke(cb, what)

    @staticmethod
    def _invoke(cb: Callable[[], object], what: str) -> None:
        try:
            cb()
        except Exception:  # noqa: BLE001 - a menu handler must never crash the pump
            log.exception("tray: %s handler raised", what)

    def _quit(self) -> None:
        # Reached from WM_COMMAND, WM_CLOSE and WM_QUERYENDSESSION, i.e. straight
        # off the ctypes WndProc boundary: route the injected callback through
        # _invoke so a raising handler cannot escape and skip PostQuitMessage.
        self._invoke(self._on_quit, "quit")
        _user32.PostQuitMessage(0)

    def set_paused(self, paused: bool) -> None:
        """Update the remembered state and, if installed, the live tooltip."""
        self._paused = paused
        if self._hwnd is not None:
            self._notify(NIM_MODIFY)

    # -- window / shell plumbing ---------------------------------------------

    def _wndproc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        if msg == WM_COMMAND:
            self._dispatch(wparam & 0xFFFF)
            return 0
        if msg == WM_TIMER and wparam == _TOOLTIP_TIMER_ID:
            if self._hwnd is not None:
                tip = self.tooltip()  # compute once; _notify reuses it below
                if tip != self._last_tip:
                    self._notify(NIM_MODIFY, tip=tip)  # a reloaded timeout reached us
            return 0
        if msg == WM_TRAYICON:
            event = lparam & 0xFFFF
            if event == WM_RBUTTONUP:
                self._show_menu()
            elif event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self._invoke(self._on_settings, "settings")
            return 0
        if msg == WM_QUERYENDSESSION:
            self._quit()
            return 1  # allow the session to end
        if msg == WM_CLOSE:
            self._quit()
            return 0
        if msg == WM_DESTROY:
            self.remove()
            _user32.PostQuitMessage(0)
            return 0
        if msg == self._taskbar_created:
            self._notify(NIM_ADD)  # Explorer restarted - put the icon back
            return 0
        return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _load_icon(self) -> int | None:
        path = paths.asset("icon-paused.ico" if self._paused else "icon.ico")
        if path is None:
            return None
        handle = _user32.LoadImageW(
            None, str(path), IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE
        )
        return handle or None

    def _destroy_icon(self) -> None:
        """Free the icon loaded by :meth:`_load_icon`.

        ``LoadImageW`` is used without ``LR_SHARED``, so every load owns a real
        HICON that must be released or the daemon leaks a GDI handle on each
        Pause toggle and each ``TaskbarCreated`` re-add.
        """
        if self._hicon is not None:
            _user32.DestroyIcon(self._hicon)
            self._hicon = None

    def _notify(self, message: int, *, tip: str | None = None) -> bool:
        """One ``Shell_NotifyIcon`` call. ``message`` is NIM_ADD/MODIFY/DELETE.

        ``tip``, when given, is a tooltip string already computed by the
        caller (the ``WM_TIMER`` handler, which must compare it against
        ``_last_tip`` before deciding to notify at all) - saves calling the
        possibly lock-taking ``status_provider`` a second time here.
        """
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = self._hwnd
        data.uID = self._ICON_UID
        if message == NIM_DELETE:
            ok = bool(_shell32.Shell_NotifyIconW(message, ctypes.byref(data)))
            if not ok:
                log.debug("Shell_NotifyIcon(DELETE) failed")
            return ok

        data.uFlags = NIF_MESSAGE | NIF_TIP
        data.uCallbackMessage = WM_TRAYICON
        if tip is None:
            tip = self.tooltip()
        tip = tip[: _TIP_MAX - 1]
        data.szTip = tip
        self._last_tip = tip
        # Reload the icon only when the paused state it reflects actually
        # changed (or none is loaded yet) - a tooltip-only update (e.g. a
        # live-reloaded timeout, polled every 2s) must not touch disk/GDI.
        if self._hicon is None or self._icon_paused_state != self._paused:
            self._destroy_icon()  # release the handle from the previous load
            self._hicon = self._load_icon()
            self._icon_paused_state = self._paused
        if self._hicon is not None:
            data.uFlags |= NIF_ICON
            data.hIcon = self._hicon
        ok = bool(_shell32.Shell_NotifyIconW(message, ctypes.byref(data)))
        if not ok:
            log.warning("Shell_NotifyIcon(%d) failed - tray icon may be missing", message)
        return ok

    def _show_menu(self) -> None:
        menu = _user32.CreatePopupMenu()
        if not menu:
            return
        _user32.AppendMenuW(menu, MF_STRING, self.ID_SETTINGS, "Settings…")
        _user32.AppendMenuW(
            menu, MF_STRING, self.ID_PAUSE, "Resume" if self._paused else "Pause"
        )
        _user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        _user32.AppendMenuW(menu, MF_STRING, self.ID_QUIT, "Quit")
        # Required so the menu dismisses when the user clicks elsewhere.
        _user32.SetForegroundWindow(self._hwnd)
        pt = wintypes.POINT()
        _user32.GetCursorPos(ctypes.byref(pt))
        # No TPM_RETURNCMD: let Windows post WM_COMMAND back to us so the
        # selection goes through the same _dispatch path as everything else.
        _user32.TrackPopupMenu(
            menu, TPM_RIGHTBUTTON, pt.x, pt.y, 0, self._hwnd, None
        )
        # MSDN Q135788: pair the SetForegroundWindow above with a posted message,
        # or the menu fails to dismiss on the first click outside it.
        _user32.PostMessageW(self._hwnd, WM_NULL, 0, 0)
        _user32.DestroyMenu(menu)

    def _register_class(self) -> None:
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._proc
        wc.hInstance = _kernel32.GetModuleHandleW(None)
        wc.lpszClassName = self._CLASS_NAME
        if not _user32.RegisterClassW(ctypes.byref(wc)):
            err = ctypes.get_last_error()
            if err != ERROR_CLASS_ALREADY_EXISTS:
                raise ctypes.WinError(err)

    def install(self) -> None:
        """Register the window class, create the hidden window, add the icon.

        Must be called on the thread that runs the message loop. Raises
        ``OSError`` on failure; the caller logs it and continues headless
        (NFR-7).
        """
        if self._hwnd is not None:
            return
        self._register_class()
        hwnd = _user32.CreateWindowExW(
            0, self._CLASS_NAME, self._WINDOW_NAME, WS_OVERLAPPED,
            0, 0, 0, 0, None, None, _kernel32.GetModuleHandleW(None), None,
        )
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self._hwnd = hwnd
        if not self._notify(NIM_ADD):
            self.remove()
            raise OSError("Shell_NotifyIcon(NIM_ADD) failed")
        if self._status_provider is not None:
            _user32.SetTimer(hwnd, _TOOLTIP_TIMER_ID, _TOOLTIP_TIMER_MS, None)
        log.info("tray icon installed")

    def remove(self) -> None:
        """Delete the icon and destroy the window.

        Idempotent and safe to re-enter: ``DestroyWindow`` dispatches
        ``WM_DESTROY`` synchronously and that handler calls back here, so
        ``_hwnd`` is cleared *before* the ``DestroyWindow`` call and the
        re-entrant call returns at the guard below.
        """
        if self._hwnd is None:
            return
        if self._status_provider is not None:
            _user32.KillTimer(self._hwnd, _TOOLTIP_TIMER_ID)
        self._notify(NIM_DELETE)  # needs _hwnd, so do it first
        self._destroy_icon()
        hwnd, self._hwnd = self._hwnd, None
        _user32.DestroyWindow(hwnd)
        log.info("tray icon removed")

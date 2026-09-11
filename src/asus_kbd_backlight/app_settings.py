"""The unelevated settings window (FR-9, FR-11, FR-13, NFR-5).

A small, transient window launched as a **separate, unelevated process** from
the tray's *Settings* entry. It reads ``config.toml``, lets the user change the
three user-facing keys, writes the file back through :func:`config.save` and
exits. The running daemon notices the changed file on its next poll and applies
it live (FR-11); this process never talks to the daemon directly.

**Toolkit.** ``customtkinter`` over stdlib ``tkinter``, as PRD section 13
specifies. A plain-``tkinter`` version shipped briefly (the R-7 fallback) and
looked the part of one: Motif-era ``tk.Scale`` sliders, hard 1-px boxed frames,
and - with no DPI awareness - a bitmap-stretched, blurry window at 125/150 %
display scaling. customtkinter brings rounded cards, a real snapping slider
(``number_of_steps``), per-monitor DPI awareness, and a dark native title bar in
dark mode. Only this process imports it; the daemon never does.

Everything that can be tested without a display lives in module-level pure
functions (:data:`PALETTE`, :func:`clamp_timeout`, :func:`index_to_level`,
:func:`level_to_index`, :func:`contrast_ratio`, :func:`build_config`,
:func:`letter_spaced`); the widget-level tests skip when Tk has no display.
"""

from __future__ import annotations

import contextlib
import ctypes
import functools
import logging
import math
import sys
from dataclasses import replace
from pathlib import Path

from asus_kbd_backlight import __version__, config, paths

log = logging.getLogger(__name__)

# --- fixed night palette (PRD section 13) -----------------------------------
PALETTE: dict[str, str] = {
    "bg": "#141824",             # window ground (deep navy)
    "surface": "#1C2233",        # the two group cards
    "border": "#2A3350",         # card outline, slider track (inactive)
    "text": "#C9D1E6",           # labels, values (soft off-white, not #FFF)
    "text-muted": "#7C89A8",     # section headers, version, tick labels
    "accent": "#9E4A6E",         # Save button, slider fill, checkbox tick
    "accent-hover": "#B25A80",   # hover
    "accent-pressed": "#83405E", # pressed / focus ring
    "accent-text": "#F0DCE6",    # text on the accent button
    "slider-knob": "#C77FA0",    # slider handle
}

TIMEOUT_MIN = 1
TIMEOUT_MAX = 60
# on_level 1/2/3 shown as three brightness detents.
LEVEL_TICKS = ("33%", "66%", "100%")

_SINGLETON_MUTEX = "AKB_SETTINGS_SINGLETON"
_WINDOW_TITLE = "asus-kbd-backlight settings"

# The singleton mutex handle is kept here for the life of the process on
# purpose: releasing it would drop the "a window is already open" signal.
_mutex_handle: int | None = None


# --- pure helpers (no display) --------------------------------------------


def clamp_timeout(raw: object, *, fallback: int = int(config.DEFAULT_TIMEOUT)) -> int:
    """Parse ``raw`` seconds and clamp to ``[1, 60]`` whole seconds.

    ``"600"`` -> 60, ``0`` -> 1, ``3.6`` -> 4, ``2.5`` -> 3 (half-up, not
    Python's round-half-to-even), anything unparseable or non-finite
    (``"inf"``, ``"1e999"``, ``"nan"``) -> ``fallback``. The slider only rests
    on whole seconds, so the entry box is normalised the same way (FR-9: "the
    box clamps to range, the two stay in sync").
    """
    try:
        value = math.floor(float(str(raw).strip()) + 0.5)
    except (TypeError, ValueError, OverflowError):
        return fallback
    return max(TIMEOUT_MIN, min(TIMEOUT_MAX, value))


def index_to_level(index: int) -> int:
    """Brightness slider detent (0/1/2) -> ``on_level`` (1/2/3)."""
    return max(0, min(2, int(index))) + 1


def level_to_index(level: int) -> int:
    """``on_level`` (1/2/3) -> brightness slider detent (0/1/2)."""
    return max(1, min(3, int(level))) - 1


def _linear(channel: int) -> float:
    c = channel / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _rgb(hex_color: str) -> tuple[int, int, int]:
    """``#rrggbb`` -> ``(r, g, b)``."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return r, g, b


def relative_luminance(hex_color: str) -> float:
    """WCAG relative luminance of an ``#rrggbb`` string."""
    r, g, b = _rgb(hex_color)
    return 0.2126 * _linear(r) + 0.7152 * _linear(g) + 0.0722 * _linear(b)


def contrast_ratio(a: str, b: str) -> float:
    """WCAG contrast ratio between two ``#rrggbb`` strings (1.0 - 21.0)."""
    hi, lo = sorted((relative_luminance(a), relative_luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def letter_spaced(text: str) -> str:
    """Section headers are letter-spaced (PRD section 13), but Tk has no
    tracking: put a hair space between the letters of each word."""
    return " ".join(" ".join(word) for word in text.split(" "))


def build_config(
    base: config.Config,
    *,
    timeout_s: int,
    level_index: int,
    autostart: bool,
    timeout_touched: bool = True,
) -> config.Config:
    """A new :class:`~asus_kbd_backlight.config.Config` from the shown values.

    ``device_id`` is carried over from ``base`` untouched: it stays file-only,
    with no control in the window (PRD open question 7). The timeout is
    carried over too while ``timeout_touched`` is false: the file may hold one
    the slider cannot show exactly (``300``, ``2.5`` - legal, but outside its
    whole 1-60 s), and opening Settings just to flip the autostart switch must
    not rewrite it. Once the user has touched the timeout controls, the shown
    value is saved - including the edge value (``60`` for a file's ``300``),
    which comparing values alone could never tell apart from "untouched".
    """
    timeout = float(clamp_timeout(timeout_s)) if timeout_touched else base.timeout
    return replace(
        base,
        timeout=timeout,
        on_level=index_to_level(level_index),
        autostart=bool(autostart),
    ).validated()


def centered_in(
    work_area: tuple[int, int, int, int], size: tuple[int, int]
) -> tuple[int, int]:
    """Top-left corner that centres a ``size`` window in a monitor's
    ``(left, top, right, bottom)`` work area (PRD section 13: "centred on the
    screen that has the cursor"). Never above/left of the work area, even for
    a window larger than it; coordinates may be negative on a monitor left of
    or above the primary one."""
    left, top, right, bottom = work_area
    width, height = size
    return (
        left + max(0, (right - left - width) // 2),
        top + max(0, (bottom - top - height) // 2),
    )


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong), ("rcMonitor", ctypes.c_long * 4),
        ("rcWork", ctypes.c_long * 4), ("dwFlags", ctypes.c_ulong),
    ]


@functools.cache
def _user32():
    """``user32`` with every prototype this module uses declared once.

    Handle-returning calls need an explicit ``restype`` or ctypes truncates the
    64-bit handle to ``c_int`` (the trap DESIGN.md records for
    ``SetWindowsHookEx``); declaring them all here means a new call site can't
    forget. Loaded lazily: only the settings process on Windows needs it.
    """
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    for name, restype, argtypes in [
        ("MessageBoxW", ctypes.c_int,
         (wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT)),
        ("FindWindowW", wintypes.HWND, (wintypes.LPCWSTR, wintypes.LPCWSTR)),
        ("IsIconic", wintypes.BOOL, (wintypes.HWND,)),
        ("ShowWindow", wintypes.BOOL, (wintypes.HWND, ctypes.c_int)),
        ("SetForegroundWindow", wintypes.BOOL, (wintypes.HWND,)),
        ("GetParent", wintypes.HWND, (wintypes.HWND,)),
        ("MonitorFromPoint", wintypes.HANDLE, (wintypes.POINT, wintypes.DWORD)),
        ("GetMonitorInfoW", wintypes.BOOL, (wintypes.HANDLE, ctypes.POINTER(_MONITORINFO))),
        ("GetWindowLongPtrW", ctypes.c_ssize_t, (wintypes.HWND, ctypes.c_int)),
        ("SetWindowLongPtrW", ctypes.c_ssize_t, (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)),
        ("SetWindowPos", wintypes.BOOL,
         (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
          ctypes.c_int, ctypes.c_int, wintypes.UINT)),
    ]:
        fn = getattr(user32, name)
        fn.restype, fn.argtypes = restype, argtypes
    return user32


def _show_error(text: str, owner: int | None = None) -> None:
    """Log ``text`` and show it in a message box: the windowed exe has no
    console, so a log line alone would mean clicking Settings (or Save)
    silently does nothing. ``owner`` - the settings window, when one exists -
    makes the box modal to it: no second Save click queued behind the box, no
    separate taskbar button, never hidden behind the window."""
    log.error("%s", text)
    if sys.platform != "win32":
        return
    MB_ICONERROR, MB_SETFOREGROUND = 0x10, 0x10000
    with contextlib.suppress(OSError):  # best effort
        _user32().MessageBoxW(owner, text, _WINDOW_TITLE, MB_ICONERROR | MB_SETFOREGROUND)


# --- single-instance guard ------------------------------------------------


def _acquire_singleton() -> bool:
    """True if this is the only settings window; False if one is already open.

    A named mutex is the cheapest cross-process latch on Windows. On anything
    else (or if the call fails) we optimistically return True - the daemon is
    Windows-only and the worst case is a second window.
    """
    global _mutex_handle
    if sys.platform != "win32":
        return True
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _mutex_handle = kernel32.CreateMutexW(None, False, _SINGLETON_MUTEX)
        return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS
    except OSError:
        return True


def _focus_existing(user32=None) -> None:
    """Bring an already-open settings window to the foreground, restoring it
    first if it is minimized - ``SetForegroundWindow`` alone leaves a
    minimized window minimized, and the second click on Settings would seem
    to do nothing. ``user32`` is injectable for tests."""
    if user32 is None:
        if sys.platform != "win32":
            return
        user32 = _user32()
    SW_RESTORE = 9
    hwnd = user32.FindWindowW(None, _WINDOW_TITLE)
    if not hwnd:
        return
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)


def _work_area_at_pointer(root) -> tuple[int, int, int, int]:
    """``(left, top, right, bottom)`` of the work area (screen minus taskbar)
    of the monitor under the mouse pointer. Falls back to Tk's idea of the
    primary screen off Windows or if the Win32 calls fail."""
    x, y = root.winfo_pointerxy()
    fallback = (0, 0, root.winfo_screenwidth(), root.winfo_screenheight())
    if sys.platform != "win32":
        return fallback
    from ctypes import wintypes

    MONITOR_DEFAULTTONEAREST = 2
    try:
        user32 = _user32()
        monitor = user32.MonitorFromPoint(wintypes.POINT(x, y), MONITOR_DEFAULTTONEAREST)
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return fallback
    except (OSError, AttributeError):
        return fallback
    left, top, right, bottom = info.rcWork
    return (left, top, right, bottom)


# --- the native frame (title bar + border) ---------------------------------
#
# customtkinter's dark mode only flips DWMWA_USE_IMMERSIVE_DARK_MODE, which
# gives Windows' generic dark-grey caption. Windows 11 (build 22000+) also
# takes explicit caption / border / caption-text colours, so the frame can
# match the palette instead of framing the navy window in a lighter band.
_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_USE_IMMERSIVE_DARK_MODE_BEFORE_20H1 = 19
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWA_BORDER_COLOR = 34
_DWMWA_CAPTION_COLOR = 35
_DWMWA_TEXT_COLOR = 36
_DWMWCP_ROUND = 2


def colorref(hex_color: str) -> int:
    """``#rrggbb`` -> a Win32 ``COLORREF``, which is ``0x00BBGGRR`` - byte
    order reversed from the hex string, the classic way to get red and blue
    swapped."""
    r, g, b = _rgb(hex_color)
    return (b << 16) | (g << 8) | r


def frame_attributes() -> list[tuple[int, int]]:
    """``(DWMWA_*, value)`` pairs that dress the native frame in the palette."""
    p = PALETTE
    return [
        (_DWMWA_USE_IMMERSIVE_DARK_MODE, 1),
        (_DWMWA_CAPTION_COLOR, colorref(p["bg"])),
        (_DWMWA_BORDER_COLOR, colorref(p["border"])),
        (_DWMWA_TEXT_COLOR, colorref(p["text-muted"])),
        (_DWMWA_WINDOW_CORNER_PREFERENCE, _DWMWCP_ROUND),
    ]


def _dwm_setter(hwnd: int):
    """``set(attribute, value) -> HRESULT`` over ``DwmSetWindowAttribute``."""
    from ctypes import wintypes

    fn = ctypes.WinDLL("dwmapi").DwmSetWindowAttribute
    fn.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
    fn.restype = ctypes.c_long  # HRESULT

    def set_attribute(attribute: int, value: int) -> int:
        data = ctypes.c_uint32(value)  # BOOL / COLORREF / enum: all 32-bit
        return fn(hwnd, attribute, ctypes.byref(data), ctypes.sizeof(data))

    return set_attribute


def style_native_frame(hwnd: int, set_attribute=None) -> None:
    """Apply :func:`frame_attributes` to ``hwnd``. Best effort, attribute by
    attribute: Windows 10 rejects the Windows 11 colour ids (the caption just
    stays dark grey), and pre-20H1 builds only know the old dark-mode id."""
    if set_attribute is None:
        if sys.platform != "win32":
            return
        try:
            set_attribute = _dwm_setter(hwnd)
        except OSError:
            return
    for attribute, value in frame_attributes():
        with contextlib.suppress(OSError):
            rc = set_attribute(attribute, value)
            if attribute == _DWMWA_USE_IMMERSIVE_DARK_MODE and rc != 0:
                set_attribute(_DWMWA_USE_IMMERSIVE_DARK_MODE_BEFORE_20H1, value)


_GWL_STYLE = -16
_WS_MAXIMIZEBOX = 0x00010000
_WS_MINIMIZEBOX = 0x00020000


def dialog_style(style: int) -> int:
    """``style`` without the minimize and maximize boxes: a small fixed
    settings dialog only needs Close. (Tk's ``resizable(False, False)`` merely
    greys the maximize box out.)"""
    return style & ~(_WS_MINIMIZEBOX | _WS_MAXIMIZEBOX)


def _window_style(hwnd: int) -> int:
    """``GetWindowLongPtrW(hwnd, GWL_STYLE)``."""
    return _user32().GetWindowLongPtrW(hwnd, _GWL_STYLE)


def _make_dialog_frame(hwnd: int) -> None:
    """Apply :func:`dialog_style` to ``hwnd`` and have Windows redraw the
    frame. Best effort."""
    SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_FRAMECHANGED = 0x1, 0x2, 0x4, 0x20
    try:
        style = _window_style(hwnd)
    except (OSError, AttributeError):  # 32-bit user32 has no *Ptr exports
        return
    if style:
        user32 = _user32()
        user32.SetWindowLongPtrW(hwnd, _GWL_STYLE, dialog_style(style))
        user32.SetWindowPos(
            hwnd, None, 0, 0, 0, 0,
            SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_FRAMECHANGED,
        )


def _toplevel_hwnd(root) -> int:
    """The real top-level window: Tk's ``winfo_id`` is a child of it."""
    return _user32().GetParent(root.winfo_id()) or 0


# --- the window ---------------------------------------------------------------

_FONT_FAMILY = "Segoe UI"
# Inner width of a card (PRD FR-9: a ~380 px wide window, 24 px outer padding,
# 16 px card padding). Sizes are customtkinter's unscaled pixels; it applies
# the monitor's DPI scaling itself.
_CONTENT_WIDTH = 300


class SettingsWindow:
    """The dark settings dialog. Constructed only when actually shown."""

    def __init__(self, cfg: config.Config, path: Path, *, save=config.save) -> None:
        import customtkinter as ctk

        self._ctk = ctk
        self._cfg = cfg
        self._path = path
        self._save = save
        self._saved = False
        self._closed = False
        # Whether the user moved the seconds slider or edited the box; until
        # then Save keeps the file's own timeout (see build_config).
        self._timeout_touched = False
        self._syncing_entry = False
        self._ticks_shown: int | None = None  # which detent the tick labels show

        p = PALETTE
        ctk.set_appearance_mode("dark")  # also switches the title bar to dark
        root = ctk.CTk(fg_color=p["bg"])
        self.root = root
        root.title(_WINDOW_TITLE)
        root.resizable(False, False)  # a fixed dialog: no maximize box either
        icon = paths.asset("icon.ico")
        if icon is not None:
            # Our keycap, not Tk's feather - and set before customtkinter's own
            # deferred default icon fires, which it skips once this was called.
            root.iconbitmap(str(icon))

        self._font =ctk.CTkFont(family=_FONT_FAMILY, size=13)
        self._font_bold = ctk.CTkFont(family=_FONT_FAMILY, size=13, weight="bold")
        self._font_small = ctk.CTkFont(family=_FONT_FAMILY, size=11)
        self._font_small_bold = ctk.CTkFont(family=_FONT_FAMILY, size=11, weight="bold")

        self._timeout = ctk.IntVar(value=clamp_timeout(cfg.timeout))
        self._level = ctk.IntVar(value=level_to_index(cfg.on_level))
        self._autostart = ctk.BooleanVar(value=cfg.autostart)

        outer = ctk.CTkFrame(root, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=24, pady=20)

        self._build_backlight_card(outer)
        self._build_startup_card(outer)
        self._build_footer(outer)

        root.protocol("WM_DELETE_WINDOW", self._on_cancel)
        root.bind("<Escape>", lambda _event: self._on_cancel())
        self._center()
        self._hwnd = _toplevel_hwnd(root) if sys.platform == "win32" else None
        if self._hwnd:
            style_native_frame(self._hwnd)
            _make_dialog_frame(self._hwnd)

    # -- construction helpers ------------------------------------------------

    def _card(self, parent, title: str):
        """A letter-spaced section header over a rounded, outlined card.
        Returns the card's padded body frame."""
        ctk, p = self._ctk, PALETTE
        ctk.CTkLabel(
            parent, text=letter_spaced(title.upper()), font=self._font_small_bold,
            text_color=p["text-muted"], anchor="w", height=16,
        ).pack(fill="x", padx=2, pady=(0, 6))
        card = ctk.CTkFrame(
            parent, fg_color=p["surface"], corner_radius=10,
            border_width=1, border_color=p["border"],
        )
        card.pack(fill="x", pady=(0, 16))
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="x", padx=16, pady=14)
        return body

    def _caption(self, parent, text: str):
        return self._ctk.CTkLabel(
            parent, text=text, font=self._font, text_color=PALETTE["text"],
            anchor="w", height=20,
        )

    def _slider(self, parent, var, lo: int, hi: int, command, width: int):
        p = PALETTE
        return self._ctk.CTkSlider(
            parent, from_=lo, to=hi, number_of_steps=hi - lo, variable=var,
            command=command, width=width, height=18,
            fg_color=p["border"], progress_color=p["accent"],
            button_color=p["slider-knob"], button_hover_color=p["accent-hover"],
        )

    def _build_backlight_card(self, parent) -> None:
        ctk, p = self._ctk, PALETTE
        body = self._card(parent, "Backlight")

        # Stay on after typing: slider + a typeable, clamping seconds box.
        self._caption(body, "Stay on after typing").pack(fill="x")
        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(fill="x", pady=(6, 14))
        self._timeout_slider = self._slider(
            row, self._timeout, TIMEOUT_MIN, TIMEOUT_MAX, self._on_slider,
            width=_CONTENT_WIDTH - 84,
        )
        self._timeout_slider.pack(side="left", fill="x", expand=True)
        self._entry_text = ctk.StringVar(value=str(self._timeout.get()))
        self._entry = ctk.CTkEntry(
            row, width=46, height=28, corner_radius=6, border_width=1,
            fg_color=p["bg"], border_color=p["border"], text_color=p["text"],
            font=self._font, justify="center", textvariable=self._entry_text,
        )
        self._entry.pack(side="left", padx=(12, 6))
        # Any edit that isn't our own _sync_entry counts as the user touching
        # the timeout - even re-typing the value already shown.
        self._entry_text.trace_add("write", self._on_entry_edited)
        self._entry.bind("<FocusOut>", self._on_entry)
        self._entry.bind("<Return>", self._on_entry)
        ctk.CTkLabel(row, text="s", font=self._font, text_color=p["text-muted"]).pack(side="left")

        # Brightness while typing: a slider that can only rest on 3 detents.
        self._caption(body, "Brightness while typing").pack(fill="x")
        self._level_slider = self._slider(
            body, self._level, 0, len(LEVEL_TICKS) - 1, self._on_level_change,
            width=_CONTENT_WIDTH,
        )
        self._level_slider.pack(fill="x", pady=(8, 2))
        ticks = ctk.CTkFrame(body, fg_color="transparent")
        ticks.pack(fill="x")
        self._tick_labels = []
        for i, text in enumerate(LEVEL_TICKS):
            ticks.grid_columnconfigure(i, weight=1, uniform="ticks")
            label = ctk.CTkLabel(ticks, text=text, height=18)
            # First under the left end, middle centred, last under the right end.
            label.grid(row=0, column=i, sticky=("w", "", "e")[i])
            self._tick_labels.append(label)
        self._refresh_level_ticks()

    def _build_startup_card(self, parent) -> None:
        p = PALETTE
        body = self._card(parent, "Startup")
        self._ctk.CTkSwitch(
            body, text="Start with Windows", variable=self._autostart,
            onvalue=True, offvalue=False, font=self._font, text_color=p["text"],
            fg_color=p["border"], progress_color=p["accent"],
            button_color=p["text"], button_hover_color=p["accent-text"],
        ).pack(anchor="w")

    def _build_footer(self, parent) -> None:
        """Version on the left, Cancel / Save on the right - Save is the only
        accent-filled control on screen."""
        ctk, p = self._ctk, PALETTE
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(
            row, text=f"v{__version__}", font=self._font_small, text_color=p["text-muted"],
        ).pack(side="left")
        ctk.CTkButton(
            row, text="Save", command=self._on_save, width=92, height=34, corner_radius=8,
            fg_color=p["accent"], hover_color=p["accent-hover"],
            text_color=p["accent-text"], font=self._font_bold,
        ).pack(side="right")
        ctk.CTkButton(
            row, text="Cancel", command=self._on_cancel, width=92, height=34, corner_radius=8,
            fg_color=p["surface"], hover_color=p["border"], border_width=1,
            border_color=p["border"], text_color=p["text"], font=self._font,
        ).pack(side="right", padx=(0, 8))

    # -- behaviour ---------------------------------------------------------

    def _sync_entry(self) -> None:
        self._syncing_entry = True
        try:
            self._entry_text.set(str(self._timeout.get()))
        finally:
            self._syncing_entry = False

    def _on_entry_edited(self, *_trace) -> None:
        if not self._syncing_entry:
            self._timeout_touched = True

    def _on_slider(self, _value: float) -> None:
        # customtkinter calls this only for the user's drag/click (never for a
        # programmatic set), after it has put the snapped second into
        # self._timeout - and on every drag motion, so only rewrite the box
        # when the second actually changed.
        self._timeout_touched = True
        if self._entry_text.get() != str(self._timeout.get()):
            self._sync_entry()

    def _on_entry(self, _event=None) -> None:
        # The slider follows the variable; an unparseable box keeps the value.
        self._timeout.set(clamp_timeout(self._entry.get(), fallback=self._timeout.get()))
        self._sync_entry()

    def _refresh_level_ticks(self) -> None:
        """Highlight the tick under the chosen detent (PRD: the selected stop
        is echoed)."""
        chosen = self._level.get()
        if chosen == self._ticks_shown:
            return  # called on every drag motion; relayout only on a new detent
        self._ticks_shown = chosen
        for i, label in enumerate(self._tick_labels):
            on = i == chosen
            label.configure(
                text_color=PALETTE["text"] if on else PALETTE["text-muted"],
                font=self._font_small_bold if on else self._font_small,
            )

    def _on_level_change(self, _value: float) -> None:
        self._refresh_level_ticks()  # the slider has already set self._level

    def _close(self) -> None:
        self._closed = True
        self.root.destroy()

    def _on_cancel(self) -> None:
        self._close()

    def _on_save(self) -> None:
        self._on_entry()  # fold any half-typed entry value in first
        new_cfg = build_config(
            self._cfg,
            timeout_s=self._timeout.get(),
            level_index=self._level.get(),
            autostart=self._autostart.get(),
            timeout_touched=self._timeout_touched,
        )
        try:
            self._save(new_cfg, self._path)
        except (OSError, ValueError) as exc:
            _show_error(f"Could not save {self._path}:\n{exc}", owner=self._hwnd)
            return  # window stays open so nothing the user set is lost
        self._saved = True
        log.info("settings saved to %s", self._path)
        self._close()

    def _center(self) -> None:
        """Centre in the work area of the monitor under the pointer. Not *on*
        the pointer: opened from the tray, that is the screen's bottom-right
        corner and half the window - Save included - would land off-screen or
        under the taskbar."""
        self.root.update_idletasks()
        size = (self.root.winfo_reqwidth(), self.root.winfo_reqheight())
        x, y = centered_in(_work_area_at_pointer(self.root), size)
        # wm_geometry, not CTk.geometry: customtkinter's override rescales the
        # numbers it is given, but these are already physical pixels (the
        # process is per-monitor DPI aware, like the monitor rectangles).
        self.root.wm_geometry(f"+{x}+{y}")

    def run(self) -> bool:
        """Show the window; block until closed. True if the config was saved."""
        self.root.mainloop()
        return self._saved


def run(config_path: Path | None = None) -> int:
    """Entry point for ``asus-kbd-backlight --settings``. Returns an exit code."""
    if not _acquire_singleton():
        log.info("a settings window is already open")
        _focus_existing()
        return 0

    path = config_path or config.default_config_path()
    try:
        # A *missing* file is not an error - config.load returns defaults, which
        # is the right first-run state. A *malformed* file raises; opening the
        # window on defaults would let Save silently overwrite a value the
        # window has no control for (device_id) with the default. Refuse
        # instead and let the user fix the file by hand.
        cfg = config.load(config_path)
    except (OSError, ValueError) as exc:
        # Unreadable, not TOML, or a value out of range - say which file and why.
        _show_error(
            f"Could not load {path}:\n{exc}\n\n"
            "Fix or delete the file, then open Settings again."
        )
        return 1

    try:
        SettingsWindow(cfg, path).run()
    except Exception as exc:  # noqa: BLE001 - a GUI failure must exit cleanly, not trace
        log.exception("settings window failed")
        # --settings writes no log file and the windowed exe has no console:
        # without this box a broken Tk/customtkinter would mean clicking
        # Settings simply does nothing. MessageBoxW doesn't need Tk.
        _show_error(f"The settings window could not open:\n{exc}")
        return 1
    return 0

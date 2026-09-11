"""The unelevated settings window (FR-9, FR-11, FR-13, NFR-5).

A small, transient window launched as a **separate, unelevated process** from
the tray's *Settings* entry. It reads ``config.toml``, lets the user change the
three user-facing keys, writes the file back through :func:`config.save` and
exits. The running daemon notices the changed file on its next poll and applies
it live (FR-11); this process never talks to the daemon directly.

**Toolkit.** Plain stdlib ``tkinter``/``ttk`` with a hand-applied dark palette,
not ``customtkinter``. Every colour token in PRD section 13 is a flat hex value
that ``tk`` widgets accept directly (``background`` / ``foreground`` /
``troughcolor`` / ``activebackground``), and the two sliders snap to whole
values with ``tk.Scale(resolution=…)`` natively. Taking the dependency would add
weight to the frozen bundle and a ``--collect-data`` step for rounded corners we
do not need on a six-control dialog. This is the R-7 fallback, chosen at
milestone 4.

Everything that can be tested without a display lives in module-level pure
functions (:data:`PALETTE`, :func:`clamp_timeout`, :func:`index_to_level`,
:func:`level_to_index`, :func:`contrast_ratio`, :func:`build_config`); the
window itself is manual per TEST-PLAN (FR-9/FR-13 are visual).
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import sys
from dataclasses import replace
from pathlib import Path

from asus_kbd_backlight import __version__, config

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

    ``"600"`` -> 60, ``0`` -> 1, ``3.6`` -> 4, anything unparseable -> ``fallback``.
    The slider only rests on whole seconds, so the entry box is normalised the
    same way (FR-9: "the box clamps to range, the two stay in sync").
    """
    try:
        value = round(float(str(raw).strip()))
    except (TypeError, ValueError):
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


def relative_luminance(hex_color: str) -> float:
    """WCAG relative luminance of an ``#rrggbb`` string."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _linear(r) + 0.7152 * _linear(g) + 0.0722 * _linear(b)


def contrast_ratio(a: str, b: str) -> float:
    """WCAG contrast ratio between two ``#rrggbb`` strings (1.0 - 21.0)."""
    hi, lo = sorted((relative_luminance(a), relative_luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def build_config(
    base: config.Config,
    *,
    timeout_s: int,
    level_index: int,
    autostart: bool,
) -> config.Config:
    """A new :class:`~asus_kbd_backlight.config.Config` from the shown values.

    ``device_id`` is carried over from ``base`` untouched: it stays file-only,
    with no control in the window (PRD open question 7).
    """
    return replace(
        base,
        timeout=float(clamp_timeout(timeout_s)),
        on_level=index_to_level(level_index),
        autostart=bool(autostart),
    ).validated()


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


def _focus_existing() -> None:
    """Bring an already-open settings window to the foreground."""
    if sys.platform != "win32":
        return
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        hwnd = user32.FindWindowW(None, _WINDOW_TITLE)
        if hwnd:
            user32.SetForegroundWindow(hwnd)
    except OSError:  # pragma: no cover - best effort
        pass


# --- the window ---------------------------------------------------------------


class SettingsWindow:
    """The dark settings dialog. Constructed only when actually shown."""

    def __init__(self, cfg: config.Config, path: Path, *, save=config.save) -> None:
        import tkinter as tk
        from tkinter import ttk

        self._tk = tk
        self._cfg = cfg
        self._path = path
        self._save = save
        self._saved = False

        p = PALETTE
        root = tk.Tk()
        self.root = root
        root.title(_WINDOW_TITLE)
        root.configure(background=p["bg"])
        root.resizable(False, False)

        self._timeout = tk.IntVar(value=clamp_timeout(cfg.timeout))
        self._level = tk.IntVar(value=level_to_index(cfg.on_level))
        self._autostart = tk.BooleanVar(value=cfg.autostart)

        style = ttk.Style(root)
        with contextlib.suppress(Exception):
            style.theme_use("clam")  # the only built-in theme that honours colours
        style.configure("TFrame", background=p["surface"])
        style.configure(
            "Card.TLabelframe", background=p["surface"], bordercolor=p["border"],
            relief="solid", borderwidth=1,
        )
        style.configure(
            "Card.TLabelframe.Label", background=p["bg"], foreground=p["text-muted"],
        )
        style.configure(
            "TCheckbutton", background=p["surface"], foreground=p["text"],
        )
        style.map("TCheckbutton", background=[("active", p["surface"])])

        outer = tk.Frame(root, background=p["bg"])
        outer.pack(fill="both", expand=True, padx=24, pady=24)

        self._build_timeout_card(outer)
        self._build_brightness_card(outer)
        self._build_autostart_row(outer)
        self._build_footer(outer)
        self._build_buttons(outer)

        root.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self._center()

    # -- construction helpers ------------------------------------------------

    def _label(self, parent, text, *, muted=False, size=13):
        p = PALETTE
        return self._tk.Label(
            parent, text=text, background=parent.cget("background"),
            foreground=p["text-muted"] if muted else p["text"],
            font=("Segoe UI", size),
        )

    def _card(self, parent, title):
        p = PALETTE
        frame = self._tk.LabelFrame(
            parent, text=title.upper(), background=p["surface"], foreground=p["text-muted"],
            bd=1, relief="solid", font=("Segoe UI", 9), labelanchor="nw",
            highlightbackground=p["border"], highlightcolor=p["border"],
        )
        frame.pack(fill="x", pady=(0, 16))
        inner = self._tk.Frame(frame, background=p["surface"])
        inner.pack(fill="x", padx=12, pady=12)
        return inner

    def _slider(self, parent, var, lo, hi, command=None):
        p = PALETTE
        return self._tk.Scale(
            parent, from_=lo, to=hi, orient="horizontal", variable=var,
            resolution=1, showvalue=False, command=command,
            background=p["surface"], foreground=p["text"], troughcolor=p["border"],
            activebackground=p["accent-hover"], highlightthickness=0,
            sliderrelief="flat", bd=0,
        )

    def _build_timeout_card(self, parent):
        p = PALETTE
        inner = self._card(parent, "Stay on after typing")
        row = self._tk.Frame(inner, background=p["surface"])
        row.pack(fill="x")

        slider = self._slider(row, self._timeout, TIMEOUT_MIN, TIMEOUT_MAX, self._on_slider)
        slider.pack(side="left", fill="x", expand=True)

        self._entry = self._tk.Entry(
            row, width=4, justify="center", background=p["bg"], foreground=p["text"],
            insertbackground=p["text"], relief="flat",
        )
        self._entry.insert(0, str(self._timeout.get()))
        self._entry.pack(side="left", padx=(12, 0))
        self._entry.bind("<FocusOut>", self._on_entry)
        self._entry.bind("<Return>", self._on_entry)

        self._label(inner, "seconds", muted=True, size=11).pack(anchor="e")

    def _build_brightness_card(self, parent):
        p = PALETTE
        inner = self._card(parent, "Brightness while typing")
        self._slider(inner, self._level, 0, 2, self._on_level_change).pack(fill="x")

        ticks = self._tk.Frame(inner, background=p["surface"])
        ticks.pack(fill="x")
        for i, text in enumerate(LEVEL_TICKS):
            ticks.columnconfigure(i, weight=1)
            self._label(ticks, text, muted=True, size=11).grid(row=0, column=i)

        self._level_echo = self._label(inner, "", muted=True, size=11)
        self._level_echo.pack(anchor="e", pady=(4, 0))
        self._refresh_level_echo()

    def _build_autostart_row(self, parent):
        from tkinter import ttk

        ttk.Checkbutton(
            parent, text="Start with Windows", variable=self._autostart,
        ).pack(anchor="w", pady=(0, 16))

    def _build_footer(self, parent):
        self._label(parent, f"v{__version__}", muted=True, size=11).pack(anchor="w")

    def _build_buttons(self, parent):
        p = PALETTE
        row = self._tk.Frame(parent, background=p["bg"])
        row.pack(fill="x", pady=(16, 0))
        self._tk.Button(
            row, text="Cancel", command=self._on_cancel, relief="flat",
            background=p["surface"], foreground=p["text"],
            activebackground=p["border"], activeforeground=p["text"], bd=0,
            padx=14, pady=6,
        ).pack(side="right")
        self._tk.Button(
            row, text="Save", command=self._on_save, relief="flat",
            background=p["accent"], foreground=p["accent-text"],
            activebackground=p["accent-hover"], activeforeground=p["accent-text"], bd=0,
            padx=18, pady=6,
        ).pack(side="right", padx=(0, 8))

    # -- behaviour ---------------------------------------------------------

    def _sync_entry(self) -> None:
        self._entry.delete(0, "end")
        self._entry.insert(0, str(self._timeout.get()))

    def _on_slider(self, _value: str) -> None:
        self._sync_entry()

    def _on_entry(self, _event=None) -> None:
        self._timeout.set(clamp_timeout(self._entry.get()))
        self._sync_entry()

    def _refresh_level_echo(self) -> None:
        self._level_echo.configure(text=LEVEL_TICKS[self._level.get()])

    def _on_level_change(self, _value: str) -> None:
        self._refresh_level_echo()

    def _on_cancel(self) -> None:
        self.root.destroy()

    def _on_save(self) -> None:
        self._on_entry()  # fold any half-typed entry value in first
        new_cfg = build_config(
            self._cfg,
            timeout_s=self._timeout.get(),
            level_index=self._level.get(),
            autostart=self._autostart.get(),
        )
        try:
            self._save(new_cfg, self._path)
        except (OSError, ValueError):
            log.exception("could not write %s", self._path)
            return
        self._saved = True
        log.info("settings saved to %s", self._path)
        self.root.destroy()

    def _center(self) -> None:
        self.root.update_idletasks()
        w, h = self.root.winfo_width(), self.root.winfo_height()
        x = self.root.winfo_pointerx() - w // 2
        y = self.root.winfo_pointery() - h // 2
        self.root.geometry(f"+{max(x, 0)}+{max(y, 0)}")

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
        log.error("%s is not valid TOML (%s) - fix it by hand, then reopen Settings", path, exc)
        return 1

    try:
        SettingsWindow(cfg, path).run()
    except Exception:  # noqa: BLE001 - a GUI failure must exit cleanly, not trace
        log.exception("settings window failed")
        return 1
    return 0

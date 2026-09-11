"""Locating bundled assets, frozen or not (FR-10).

PyInstaller unpacks ``--add-data`` payloads into a temporary directory whose
path it exposes as ``sys._MEIPASS``. Running from a source checkout there is no
such directory and the files sit in ``assets/`` next to the package.
"""

from __future__ import annotations

import sys
from pathlib import Path


def asset_dir() -> Path:
    """Directory holding the bundled assets. May not exist."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass is not None:  # frozen: --add-data "assets;assets"
        return Path(meipass) / "assets"
    # src/asus_kbd_backlight/paths.py -> repo root
    return Path(__file__).resolve().parents[2] / "assets"


def asset(name: str) -> Path | None:
    """Path to a bundled asset, or ``None`` if it is not there.

    Callers must tolerate ``None``: a missing icon degrades the tray to its
    default icon, it does not stop the daemon (NFR-7).
    """
    path = asset_dir() / name
    return path if path.is_file() else None


# build.ps1 names the console build after the windowed one plus this suffix
# (asus-kbd-backlight.exe / asus-kbd-backlight-debug.exe).
_DEBUG_SUFFIX = "-debug"


def windowless_executable() -> str:
    """The console-less program to start a child process with.

    Frozen: the running exe itself, unless it is the console *debug* build -
    then its windowed twin (``foo-debug.exe`` -> ``foo.exe``) next to it, so
    the settings window or the logon task never pops up a console. The twin is
    derived from the running exe's own name rather than a hard-coded one, so a
    renamed download (``asus-kbd-backlight (1).exe``) never hands off to an
    older copy that happens to sit in the same folder. From source:
    ``pythonw.exe`` next to the interpreter, for the same reason. Falls back to
    ``sys.executable`` when that sibling isn't there.
    """
    exe = Path(sys.executable)
    if getattr(sys, "frozen", False):
        # A non-debug exe maps to itself.
        candidate = exe.with_name(exe.stem.removesuffix(_DEBUG_SUFFIX) + exe.suffix)
    else:
        candidate = exe.with_name("pythonw.exe")
    return str(candidate) if candidate.is_file() else sys.executable

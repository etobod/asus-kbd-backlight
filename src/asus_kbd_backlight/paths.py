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

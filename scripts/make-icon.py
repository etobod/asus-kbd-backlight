"""Generate assets/icon.ico and assets/icon-paused.ico (FR-10).

Stdlib only — no Pillow, no ImageMagick, no Inkscape. The icon is drawn
procedurally rather than rasterised from an SVG so that regenerating it needs
nothing but the interpreter that already builds the project:

    python scripts/make-icon.py

The design (PRD s13 palette): a backlit keycap on a deep navy ground with a
soft rose glow leaking out from under it. The paused variant is the same shape
desaturated, with the glow removed - it must read as "off" at 16 px.

Both .ico files are committed, so this script only runs when the design
changes.
"""

from __future__ import annotations

import math
import struct
import sys
import zlib
from pathlib import Path

SIZES = (16, 24, 32, 48, 256)
SS = 4  # supersampling factor -> cheap antialiasing

# Palette (PRD s13). RGB, 0-255.
GLOW = (199, 127, 160)      # slider-knob rose, used for the light leak
CAP_TOP = (44, 54, 80)      # keycap face, lit edge
CAP_BOTTOM = (22, 27, 41)   # keycap face, shadowed edge
RIM = (58, 70, 104)         # border highlight
LEGEND = (201, 209, 230)    # text: the bar printed on the keycap

Pixel = tuple[int, int, int, int]


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _mix(bottom: Pixel, top: Pixel) -> Pixel:
    """Source-over composite of ``top`` onto ``bottom``, both straight alpha."""
    ta = top[3] / 255.0
    if ta <= 0.0:
        return bottom
    ba = bottom[3] / 255.0
    out_a = ta + ba * (1.0 - ta)
    if out_a <= 0.0:
        return (0, 0, 0, 0)
    out = [
        int(round((top[i] * ta + bottom[i] * ba * (1.0 - ta)) / out_a)) for i in range(3)
    ]
    return (out[0], out[1], out[2], int(round(out_a * 255)))


def _rounded_rect_inside(x: float, y: float, x0: float, y0: float, x1: float, y1: float,
                         r: float) -> bool:
    """Point-in-rounded-rectangle: distance to the corner-centre rectangle."""
    r = min(r, (x1 - x0) / 2, (y1 - y0) / 2)
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    return math.hypot(x - cx, y - cy) <= r


def _sample(u: float, v: float, *, paused: bool) -> Pixel:
    """Colour of the icon at normalised coordinates ``u``, ``v`` in [0, 1]."""
    px: Pixel = (0, 0, 0, 0)

    # 1. The glow: a soft radial bloom centred just below the keycap, so the
    #    light reads as coming out from under it. Dropped for the paused icon.
    if not paused:
        d = math.hypot((u - 0.5) / 0.60, (v - 0.80) / 0.34)
        if d < 1.0:
            t = 1.0 - d
            alpha = int(round(215 * t * t))
            if alpha > 0:
                px = _mix(px, (*GLOW, alpha))

    # 2. The keycap body.
    x0, y0, x1, y1 = 0.17, 0.13, 0.83, 0.73
    if _rounded_rect_inside(u, v, x0, y0, x1, y1, 0.13):
        t = (v - y0) / (y1 - y0)
        body = tuple(int(round(_lerp(CAP_TOP[i], CAP_BOTTOM[i], t))) for i in range(3))
        px = _mix(px, (*body, 255))

        # Rim: the outer 1.5% of the cap, brighter at the top.
        inner = _rounded_rect_inside(u, v, x0 + 0.022, y0 + 0.022, x1 - 0.022, y1 - 0.022, 0.115)
        if not inner:
            px = _mix(px, (*RIM, 210 if v < 0.45 else 130))

        # Legend: a single bar, the abstraction of a key's printed glyph.
        if _rounded_rect_inside(u, v, 0.31, 0.37, 0.69, 0.47, 0.05):
            px = _mix(px, (*LEGEND, 255 if not paused else 150))

    if paused:
        # Desaturate everything that survived: the tray must show "off" at a
        # glance, without relying on colour vision (FR-10).
        grey = int(round(0.299 * px[0] + 0.587 * px[1] + 0.114 * px[2]))
        px = (grey, grey, grey, px[3])

    return px


def render(size: int, *, paused: bool) -> bytes:
    """Render one RGBA raster, supersampled ``SS``x and box-filtered down."""
    rows: list[bytes] = []
    n = SS * SS
    for py in range(size):
        row = bytearray()
        for px_ in range(size):
            acc = [0, 0, 0, 0]
            for sy in range(SS):
                v = (py + (sy + 0.5) / SS) / size
                for sx in range(SS):
                    u = (px_ + (sx + 0.5) / SS) / size
                    s = _sample(u, v, paused=paused)
                    # Premultiply before averaging, or edge pixels pick up the
                    # colour of fully transparent samples.
                    a = s[3]
                    acc[0] += s[0] * a
                    acc[1] += s[1] * a
                    acc[2] += s[2] * a
                    acc[3] += a
            alpha = acc[3] / n
            if alpha <= 0.5:
                row += b"\x00\x00\x00\x00"
                continue
            row += bytes(
                (
                    min(255, int(round(acc[0] / acc[3]))),
                    min(255, int(round(acc[1] / acc[3]))),
                    min(255, int(round(acc[2] / acc[3]))),
                    min(255, int(round(alpha))),
                )
            )
        rows.append(bytes(row))
    return _png(size, size, rows)


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    )


def _png(width: int, height: int, rows: list[bytes]) -> bytes:
    raw = b"".join(b"\x00" + r for r in rows)  # filter type 0 on every scanline
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )


def build_ico(*, paused: bool) -> bytes:
    """A multi-resolution .ico with PNG-compressed entries (Vista+)."""
    images = [render(s, paused=paused) for s in SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, blobs = b"", b""
    for size, data in zip(SIZES, images):
        dim = 0 if size >= 256 else size  # 0 means 256 in an ICONDIRENTRY
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
        blobs += data
    return header + entries + blobs


def main() -> int:
    out = Path(__file__).resolve().parents[1] / "assets"
    out.mkdir(exist_ok=True)
    for name, paused in (("icon.ico", False), ("icon-paused.ico", True)):
        path = out / name
        path.write_bytes(build_ico(paused=paused))
        print(f"wrote {path} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

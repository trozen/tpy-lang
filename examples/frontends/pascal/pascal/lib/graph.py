"""TPy-side implementation of Turbo Pascal's `Graph` unit (BGI),
Tier-A subset: open / close / clear / pixel / line / rect / bar /
circle / pen-color / canvas dims.

Imported via Pascal `uses Graph;`, which lowers to a star import
of bare `graph` (the file's stem). The visible surface is
lowercase to match what the Pascal lexer canonicalises identifiers
to: `initgraph`, `closegraph`, `setcolor`, etc.

All drawing happens in pure TPy against a packed `Int32` pixel
buffer (one RGBA-packed value per pixel). On `closegraph` the
buffer is dumped to a P3 PPM file (`out.ppm`) in the program's
cwd so users without an interactive backend can still view the
result; tests don't snapshot the PPM, they read pixels back via
`getpixel` and snapshot the text output instead.

An optional SDL2 display layer will land in a later milestone --
this file is its own complete drawing implementation regardless.
"""

from __future__ import annotations

from tpy import Int32


# TP7 16-color palette -> 0xRRGGBB packed. We drop the alpha
# channel (Int32 fits 24-bit RGB but not 32-bit ARGB); when an
# SDL display layer ships, the present-loop synthesises alpha
# (=0xFF) at upload time.
_PALETTE: list[Int32] = [
    0x000000,  # 0  Black
    0x0000AA,  # 1  Blue
    0x00AA00,  # 2  Green
    0x00AAAA,  # 3  Cyan
    0xAA0000,  # 4  Red
    0xAA00AA,  # 5  Magenta
    0xAA5500,  # 6  Brown
    0xAAAAAA,  # 7  LightGray
    0x555555,  # 8  DarkGray
    0x5555FF,  # 9  LightBlue
    0x55FF55,  # 10 LightGreen
    0x55FFFF,  # 11 LightCyan
    0xFF5555,  # 12 LightRed
    0xFF55FF,  # 13 LightMagenta
    0xFFFF55,  # 14 Yellow
    0xFFFFFF,  # 15 White
]


class GraphContext:
    """Singleton state for an open BGI session. TP7 only supports
    one graphics mode at a time, so a module-level instance is
    sufficient. Fields are reset by `closegraph` so back-to-back
    init/close cycles work."""

    width: Int32
    height: Int32
    fg: Int32        # current pen palette index (0..15)
    bg: Int32        # current background palette index
    pen_x: Int32     # last drawn point (for `lineto`/`moveto` in Tier B)
    pen_y: Int32
    pixels: list[Int32]  # length = width * height, packed AARRGGBB

    def __init__(self) -> None:
        self.width = Int32(0)
        self.height = Int32(0)
        self.fg = Int32(15)
        self.bg = Int32(0)
        self.pen_x = Int32(0)
        self.pen_y = Int32(0)
        self.pixels = []

    def is_open(self) -> bool:
        return self.width > 0 and self.height > 0


# Module-level singleton. Pascal source sees the procedure-style
# API; this object is internal.
_ctx: GraphContext = GraphContext()


def _resolve_color(c: Int32) -> Int32:
    """Map a TP7 palette index (0..15) to packed AARRGGBB. Indices
    outside the range default to white -- TP7 itself is lenient
    about out-of-range color values."""
    if c < 0 or c >= 16:
        return _PALETTE[15]
    return _PALETTE[c]


def _set_pixel_raw(x: Int32, y: Int32, packed: Int32) -> None:
    """Write a packed color to (x, y) if the coordinates are inside
    the canvas. Out-of-range writes are silently dropped -- BGI's
    `PutPixel` behaves the same."""
    if x < 0 or x >= _ctx.width:
        return
    if y < 0 or y >= _ctx.height:
        return
    _ctx.pixels[y * _ctx.width + x] = packed


def initgraph(driver: Int32, mode: Int32, path: str) -> None:
    """Open a graphics canvas. The TP7 `driver` / `mode` /
    `pathtodriver` args are accepted for source-language fidelity;
    we pick a fixed canvas size (320x200) when `driver=0`
    (Detect). Non-zero `mode` lets a Pascal program request a
    different size by smuggling `width*1000 + height` -- a
    convenience-only extension; standard TP7 programs leave both
    at zero and rely on the default."""
    if mode > 0:
        width = mode // 1000
        height = mode % 1000
    else:
        width = 320
        height = 200
    _ctx.width = Int32(width)
    _ctx.height = Int32(height)
    _ctx.fg = Int32(15)
    _ctx.bg = Int32(0)
    _ctx.pen_x = Int32(0)
    _ctx.pen_y = Int32(0)
    bg_packed = _resolve_color(_ctx.bg)
    _ctx.pixels = [bg_packed] * (width * height)


def closegraph() -> None:
    """Flush the canvas to `out.ppm` (ASCII P3 format) in the
    program's cwd. The file is written even when the canvas is
    empty so the user always finds out where the snapshot landed.
    State is reset afterwards so `initgraph` can be called again."""
    if _ctx.is_open():
        _write_ppm("out.ppm")
    _ctx.width = Int32(0)
    _ctx.height = Int32(0)
    _ctx.pixels = []


def _write_ppm(filename: str) -> None:
    """Dump the canvas as a P3 PPM. Each pixel is three decimal
    bytes separated by spaces; rows separated by newlines. The
    format is ASCII so users can inspect it directly, and any image
    viewer reads it natively."""
    lines: list[str] = []
    lines.append("P3")
    lines.append(str(_ctx.width) + " " + str(_ctx.height))
    lines.append("255")
    n = _ctx.width * _ctx.height
    i = Int32(0)
    parts: list[str] = []
    while i < n:
        packed = _ctx.pixels[i]
        r = (packed >> 16) & 0xFF
        g = (packed >> 8) & 0xFF
        b = packed & 0xFF
        parts.append(str(r) + " " + str(g) + " " + str(b))
        i += 1
        if i % _ctx.width == 0:
            lines.append(" ".join(parts))
            parts = []
    with open(filename, "w") as f:
        for line in lines:
            f.write(line)
            f.write("\n")


def setcolor(c: Int32) -> None:
    _ctx.fg = c


def setbkcolor(c: Int32) -> None:
    _ctx.bg = c


def cleardevice() -> None:
    """Fill the entire canvas with the current background color."""
    bg_packed = _resolve_color(_ctx.bg)
    i = Int32(0)
    n = _ctx.width * _ctx.height
    while i < n:
        _ctx.pixels[i] = bg_packed
        i += 1


def putpixel(x: Int32, y: Int32, c: Int32) -> None:
    _set_pixel_raw(x, y, _resolve_color(c))


def getpixel(x: Int32, y: Int32) -> Int32:
    """Return the palette index of the pixel at (x, y). Out-of-
    range reads return 0 (black). The implementation scans the
    palette for an exact RGBA match; TP7 BGI does the same since
    `PutPixel` always writes a palette color."""
    if x < 0 or x >= _ctx.width:
        return Int32(0)
    if y < 0 or y >= _ctx.height:
        return Int32(0)
    packed = _ctx.pixels[y * _ctx.width + x]
    i = Int32(0)
    while i < 16:
        if _PALETTE[i] == packed:
            return i
        i += 1
    return Int32(0)


def line(x1: Int32, y1: Int32, x2: Int32, y2: Int32) -> None:
    """Bresenham's line algorithm. Works for arbitrary directions
    including degenerate (single-pixel) and axis-aligned cases."""
    packed = _resolve_color(_ctx.fg)
    x = x1
    y = y1
    dx = x2 - x1
    if dx < 0:
        dx = -dx
    dy = y2 - y1
    if dy < 0:
        dy = -dy
    sx = Int32(1) if x1 < x2 else Int32(-1)
    sy = Int32(1) if y1 < y2 else Int32(-1)
    err = dx - dy
    while True:
        _set_pixel_raw(x, y, packed)
        if x == x2 and y == y2:
            return
        e2 = err * 2
        if e2 > -dy:
            err -= dy
            x += sx
        if e2 < dx:
            err += dx
            y += sy


def rectangle(x1: Int32, y1: Int32, x2: Int32, y2: Int32) -> None:
    """Outline of an axis-aligned rectangle. Lower-right corner is
    inclusive (matches BGI). Caller is responsible for any
    coordinate normalisation -- a swapped pair still draws but in
    a non-canonical traversal order."""
    line(x1, y1, x2, y1)
    line(x2, y1, x2, y2)
    line(x2, y2, x1, y2)
    line(x1, y2, x1, y1)


def bar(x1: Int32, y1: Int32, x2: Int32, y2: Int32) -> None:
    """Solid-filled rectangle. The fill color is the current pen
    color (TP7 BGI's `Bar` uses the current fill style, which our
    Tier-A subset collapses to the pen color)."""
    packed = _resolve_color(_ctx.fg)
    lo_x = x1 if x1 <= x2 else x2
    hi_x = x2 if x1 <= x2 else x1
    lo_y = y1 if y1 <= y2 else y2
    hi_y = y2 if y1 <= y2 else y1
    y = lo_y
    while y <= hi_y:
        x = lo_x
        while x <= hi_x:
            _set_pixel_raw(x, y, packed)
            x += 1
        y += 1


def circle(cx: Int32, cy: Int32, r: Int32) -> None:
    """Midpoint circle algorithm. Plots eight symmetric octants
    per step so the outline is connected even at low radii."""
    packed = _resolve_color(_ctx.fg)
    x = Int32(0)
    y = r
    d = Int32(3) - 2 * r
    while x <= y:
        _set_pixel_raw(cx + x, cy + y, packed)
        _set_pixel_raw(cx - x, cy + y, packed)
        _set_pixel_raw(cx + x, cy - y, packed)
        _set_pixel_raw(cx - x, cy - y, packed)
        _set_pixel_raw(cx + y, cy + x, packed)
        _set_pixel_raw(cx - y, cy + x, packed)
        _set_pixel_raw(cx + y, cy - x, packed)
        _set_pixel_raw(cx - y, cy - x, packed)
        if d < 0:
            d += 4 * x + 6
        else:
            d += 4 * (x - y) + 10
            y -= 1
        x += 1


def getmaxx() -> Int32:
    """Highest valid x coordinate (width - 1). TP7 BGI convention."""
    return _ctx.width - 1


def getmaxy() -> Int32:
    return _ctx.height - 1

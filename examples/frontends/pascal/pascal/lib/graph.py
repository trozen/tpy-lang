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


# Pen state (Tier B) -----------------------------------------------
#
# TP7 BGI's `MoveTo` / `LineTo` form a "turtle graphics" pattern --
# the current pen position is mutated by `MoveTo` and read by
# `LineTo` (which then advances to the destination). `Line(...)`
# itself does NOT move the pen; only `LineTo` does.


def moveto(x: Int32, y: Int32) -> None:
    _ctx.pen_x = x
    _ctx.pen_y = y


def lineto(x: Int32, y: Int32) -> None:
    """Draw a line from the current pen position to (x, y), then
    advance the pen there."""
    line(_ctx.pen_x, _ctx.pen_y, x, y)
    _ctx.pen_x = x
    _ctx.pen_y = y


def getx() -> Int32:
    return _ctx.pen_x


def gety() -> Int32:
    return _ctx.pen_y


# Bitmap font (Tier B) ---------------------------------------------
#
# 8x8 ASCII font, MSB-first per row. Lookup: `(ord(c) - 32) * 8`
# is the offset of the first row's byte. Coverage is a Tier-B-
# minimum subset -- space, digits, uppercase A-Z, and a handful
# of common punctuation; lowercase is rendered as uppercase
# (Pascal source for kid programs typically labels axes with
# uppercase anyway). Unknown / out-of-range chars render as a
# fully-blank cell so missing glyphs don't crash the output.

_FONT_FIRST: Int32 = Int32(32)
_FONT_LAST: Int32 = Int32(95)  # exclusive (' ' through '_')
_FONT_BYTES_PER_CHAR: Int32 = Int32(8)

_FONT: list[Int32] = [
    # ' ' (32)
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    # '!' (33)
    0x18, 0x18, 0x18, 0x18, 0x00, 0x00, 0x18, 0x00,
    # '"' (34) -- blank
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    # '#' (35) -- blank
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    # '$' (36) -- blank
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    # '%' (37) -- blank
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    # '&' (38) -- blank
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    # "'" (39) -- blank
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    # '(' (40)
    0x0C, 0x18, 0x30, 0x30, 0x30, 0x18, 0x0C, 0x00,
    # ')' (41)
    0x30, 0x18, 0x0C, 0x0C, 0x0C, 0x18, 0x30, 0x00,
    # '*' (42) -- blank
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    # '+' (43)
    0x00, 0x18, 0x18, 0x7E, 0x18, 0x18, 0x00, 0x00,
    # ',' (44)
    0x00, 0x00, 0x00, 0x00, 0x00, 0x18, 0x18, 0x30,
    # '-' (45)
    0x00, 0x00, 0x00, 0x7E, 0x00, 0x00, 0x00, 0x00,
    # '.' (46)
    0x00, 0x00, 0x00, 0x00, 0x00, 0x18, 0x18, 0x00,
    # '/' (47) -- blank
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    # '0' (48)
    0x3C, 0x66, 0x6E, 0x76, 0x66, 0x66, 0x3C, 0x00,
    # '1' (49)
    0x18, 0x38, 0x18, 0x18, 0x18, 0x18, 0x7E, 0x00,
    # '2' (50)
    0x3C, 0x66, 0x06, 0x0C, 0x18, 0x30, 0x7E, 0x00,
    # '3' (51)
    0x3C, 0x66, 0x06, 0x1C, 0x06, 0x66, 0x3C, 0x00,
    # '4' (52)
    0x0C, 0x1C, 0x2C, 0x4C, 0x7E, 0x0C, 0x0C, 0x00,
    # '5' (53)
    0x7E, 0x60, 0x7C, 0x06, 0x06, 0x66, 0x3C, 0x00,
    # '6' (54)
    0x1C, 0x30, 0x60, 0x7C, 0x66, 0x66, 0x3C, 0x00,
    # '7' (55)
    0x7E, 0x06, 0x0C, 0x18, 0x18, 0x18, 0x18, 0x00,
    # '8' (56)
    0x3C, 0x66, 0x66, 0x3C, 0x66, 0x66, 0x3C, 0x00,
    # '9' (57)
    0x3C, 0x66, 0x66, 0x3E, 0x06, 0x0C, 0x38, 0x00,
    # ':' (58)
    0x00, 0x18, 0x18, 0x00, 0x00, 0x18, 0x18, 0x00,
    # ';' (59) -- blank
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    # '<' (60) -- blank
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    # '=' (61)
    0x00, 0x00, 0x7E, 0x00, 0x7E, 0x00, 0x00, 0x00,
    # '>' (62) -- blank
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    # '?' (63)
    0x3C, 0x66, 0x06, 0x0C, 0x18, 0x00, 0x18, 0x00,
    # '@' (64) -- blank
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    # 'A' (65)
    0x18, 0x3C, 0x66, 0x66, 0x7E, 0x66, 0x66, 0x00,
    # 'B' (66)
    0x7C, 0x66, 0x66, 0x7C, 0x66, 0x66, 0x7C, 0x00,
    # 'C' (67)
    0x3C, 0x66, 0x60, 0x60, 0x60, 0x66, 0x3C, 0x00,
    # 'D' (68)
    0x78, 0x6C, 0x66, 0x66, 0x66, 0x6C, 0x78, 0x00,
    # 'E' (69)
    0x7E, 0x60, 0x60, 0x7C, 0x60, 0x60, 0x7E, 0x00,
    # 'F' (70)
    0x7E, 0x60, 0x60, 0x7C, 0x60, 0x60, 0x60, 0x00,
    # 'G' (71)
    0x3C, 0x66, 0x60, 0x6E, 0x66, 0x66, 0x3C, 0x00,
    # 'H' (72)
    0x66, 0x66, 0x66, 0x7E, 0x66, 0x66, 0x66, 0x00,
    # 'I' (73)
    0x3C, 0x18, 0x18, 0x18, 0x18, 0x18, 0x3C, 0x00,
    # 'J' (74)
    0x1E, 0x0C, 0x0C, 0x0C, 0x0C, 0x6C, 0x38, 0x00,
    # 'K' (75)
    0x66, 0x6C, 0x78, 0x70, 0x78, 0x6C, 0x66, 0x00,
    # 'L' (76)
    0x60, 0x60, 0x60, 0x60, 0x60, 0x60, 0x7E, 0x00,
    # 'M' (77)
    0x63, 0x77, 0x7F, 0x6B, 0x63, 0x63, 0x63, 0x00,
    # 'N' (78)
    0x66, 0x76, 0x7E, 0x7E, 0x6E, 0x66, 0x66, 0x00,
    # 'O' (79)
    0x3C, 0x66, 0x66, 0x66, 0x66, 0x66, 0x3C, 0x00,
    # 'P' (80)
    0x7C, 0x66, 0x66, 0x7C, 0x60, 0x60, 0x60, 0x00,
    # 'Q' (81)
    0x3C, 0x66, 0x66, 0x66, 0x6A, 0x6C, 0x36, 0x00,
    # 'R' (82)
    0x7C, 0x66, 0x66, 0x7C, 0x78, 0x6C, 0x66, 0x00,
    # 'S' (83)
    0x3C, 0x66, 0x60, 0x3C, 0x06, 0x66, 0x3C, 0x00,
    # 'T' (84)
    0x7E, 0x18, 0x18, 0x18, 0x18, 0x18, 0x18, 0x00,
    # 'U' (85)
    0x66, 0x66, 0x66, 0x66, 0x66, 0x66, 0x3C, 0x00,
    # 'V' (86)
    0x66, 0x66, 0x66, 0x66, 0x66, 0x3C, 0x18, 0x00,
    # 'W' (87)
    0x63, 0x63, 0x63, 0x6B, 0x7F, 0x77, 0x63, 0x00,
    # 'X' (88)
    0x66, 0x66, 0x3C, 0x18, 0x3C, 0x66, 0x66, 0x00,
    # 'Y' (89)
    0x66, 0x66, 0x66, 0x3C, 0x18, 0x18, 0x18, 0x00,
    # 'Z' (90)
    0x7E, 0x06, 0x0C, 0x18, 0x30, 0x60, 0x7E, 0x00,
    # '[' (91) -- blank
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    # '\\' (92) -- blank
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    # ']' (93) -- blank
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    # '^' (94) -- blank
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
]


def _glyph_offset(c: Int32) -> Int32:
    """Byte offset of the glyph for code `c` in `_FONT`, or -1 if
    out of range. Lowercase ASCII folds to uppercase so labels
    typed in either case render."""
    code = c
    if code >= Int32(97) and code <= Int32(122):
        code = code - Int32(32)  # 'a'..'z' -> 'A'..'Z'
    if code < _FONT_FIRST or code >= _FONT_LAST:
        return Int32(-1)
    return (code - _FONT_FIRST) * _FONT_BYTES_PER_CHAR


def _draw_glyph(x: Int32, y: Int32, c: Int32,
                packed: Int32) -> None:
    """Plot a single 8x8 glyph at (x, y). Each set bit in the
    glyph's row bytes becomes a pixel in the current pen color."""
    offset = _glyph_offset(c)
    if offset < 0:
        return
    row = Int32(0)
    while row < Int32(8):
        bits = _FONT[offset + row]
        col = Int32(0)
        while col < Int32(8):
            mask = Int32(1) << (Int32(7) - col)
            if (bits & mask) != 0:
                _set_pixel_raw(x + col, y + row, packed)
            col += 1
        row += 1


def outtextxy(x: Int32, y: Int32, s: str) -> None:
    """Draw a string at (x, y) using the embedded 8x8 bitmap font
    in the current pen color. Each character advances the
    horizontal cursor by 8 pixels; unsupported characters render
    as a blank cell. Does NOT update the BGI pen position --
    matches TP7's behaviour."""
    packed = _resolve_color(_ctx.fg)
    cx = x
    i = Int32(0)
    n = len(s)
    while i < n:
        _draw_glyph(cx, y, Int32(ord(s[i])), packed)
        cx += Int32(8)
        i += 1


def outtext(s: str) -> None:
    """Draw a string at the current pen position. Like
    `outtextxy` the pen is not advanced (BGI mirrors that)."""
    outtextxy(_ctx.pen_x, _ctx.pen_y, s)


# Ellipse (Tier B) -------------------------------------------------
#
# Midpoint ellipse algorithm. Plots four symmetric quadrants per
# step; switches between the two regions when the slope crosses
# -1. `ellipse` draws an outline; `fillellipse` fills it with a
# per-scanline horizontal-extent table.


def ellipse(cx: Int32, cy: Int32, rx: Int32, ry: Int32) -> None:
    """Outline of an axis-aligned ellipse centred at (cx, cy) with
    horizontal radius `rx` and vertical radius `ry`. Degenerate
    cases (`rx==0` or `ry==0`) fall back to a single straight
    line, matching what BGI does on real hardware."""
    if rx == 0 and ry == 0:
        _set_pixel_raw(cx, cy, _resolve_color(_ctx.fg))
        return
    if rx == 0:
        line(cx, cy - ry, cx, cy + ry)
        return
    if ry == 0:
        line(cx - rx, cy, cx + rx, cy)
        return
    packed = _resolve_color(_ctx.fg)
    rx_sq = rx * rx
    ry_sq = ry * ry
    x = Int32(0)
    y = ry
    # Region 1: slope > -1
    p = ry_sq - rx_sq * ry + (rx_sq // 4)
    while (ry_sq * x) < (rx_sq * y):
        _set_pixel_raw(cx + x, cy + y, packed)
        _set_pixel_raw(cx - x, cy + y, packed)
        _set_pixel_raw(cx + x, cy - y, packed)
        _set_pixel_raw(cx - x, cy - y, packed)
        x += 1
        if p < 0:
            p += 2 * ry_sq * x + ry_sq
        else:
            y -= 1
            p += 2 * ry_sq * x - 2 * rx_sq * y + ry_sq
    # Region 2: slope <= -1
    p = ((ry_sq * (2 * x + 1) * (2 * x + 1)) // 4
         + rx_sq * (y - 1) * (y - 1) - rx_sq * ry_sq)
    while y >= 0:
        _set_pixel_raw(cx + x, cy + y, packed)
        _set_pixel_raw(cx - x, cy + y, packed)
        _set_pixel_raw(cx + x, cy - y, packed)
        _set_pixel_raw(cx - x, cy - y, packed)
        y -= 1
        if p > 0:
            p += rx_sq - 2 * rx_sq * y
        else:
            x += 1
            p += 2 * ry_sq * x - 2 * rx_sq * y + rx_sq


def fillellipse(cx: Int32, cy: Int32, rx: Int32, ry: Int32) -> None:
    """Filled ellipse. Scans every y-row in `[cy-ry, cy+ry]` and
    fills a horizontal extent computed from the ellipse equation:
    `(x/rx)^2 + (y/ry)^2 <= 1`, solved for x. Faster than a
    point-by-point midpoint-fill and avoids the
    boundary-pixel double-write the outline algorithm would
    duplicate."""
    if rx == 0 and ry == 0:
        _set_pixel_raw(cx, cy, _resolve_color(_ctx.fg))
        return
    if rx == 0:
        line(cx, cy - ry, cx, cy + ry)
        return
    if ry == 0:
        line(cx - rx, cy, cx + rx, cy)
        return
    packed = _resolve_color(_ctx.fg)
    rx_sq = rx * rx
    ry_sq = ry * ry
    dy = -ry
    while dy <= ry:
        # x_extent^2 = rx^2 * (1 - dy^2 / ry^2)
        # = rx^2 - rx^2 * dy^2 / ry^2
        # Integer form: rx_sq * (ry_sq - dy*dy) / ry_sq
        num = rx_sq * (ry_sq - dy * dy)
        # Integer sqrt: linear scan up to rx (small, fine for
        # the canvas sizes the POC handles).
        ext = Int32(0)
        while (ext + 1) * (ext + 1) * ry_sq <= num:
            ext += 1
        x = cx - ext
        x_end = cx + ext
        y = cy + dy
        while x <= x_end:
            _set_pixel_raw(x, y, packed)
            x += 1
        dy += 1


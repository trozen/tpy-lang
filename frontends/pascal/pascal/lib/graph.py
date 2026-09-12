"""TPy-side implementation of Turbo Pascal's `Graph` unit (BGI),
Tier-A subset: open / close / clear / pixel / line / rect / bar /
circle / pen-color / canvas dims.

Imported via Pascal `uses Graph;`, which lowers to a star import
of bare `graph` (the file's stem). The visible surface is
lowercase to match what the Pascal lexer canonicalises identifiers
to: `initgraph`, `closegraph`, `setcolor`, etc.

All drawing happens in pure TPy against a packed `int32` pixel
buffer (one RGBA-packed value per pixel). On `closegraph` the
buffer is dumped to a P3 PPM file (`out.ppm`) in the program's
cwd so users without an interactive backend can still view the
result; tests don't snapshot the PPM, they read pixels back via
`getpixel` and snapshot the text output instead.

An optional SDL2 display layer will land in a later milestone --
this file is its own complete drawing implementation regardless.
"""

from __future__ import annotations

import math as _math

from tpy import Array, int32, Span


# TP7 BGI graphics-driver constants. Programs typically write
# `gd := VGA; gm := VGAMed; InitGraph(gd, gm, '')` -- the names are
# expected to exist as module-level constants. The POC ignores the
# driver/mode pair (initgraph hard-codes the canvas size); the
# constants are here so user code that mentions them resolves.
# Names are lowercased because the Pascal frontend canonicalises
# identifiers to lowercase at parse time -- a Pascal `VGA` and
# `vga` both look up the symbol `vga` in this module.
detect: int32 = int32(0)
cga: int32 = int32(1)
mcga: int32 = int32(2)
ega: int32 = int32(3)
ega64: int32 = int32(4)
egamono: int32 = int32(5)
ibm8514: int32 = int32(6)
hercmono: int32 = int32(7)
att400: int32 = int32(8)
vga: int32 = int32(9)
pc3270: int32 = int32(10)
currentdriver: int32 = int32(-128)

# BGI graphics-mode constants. The POC only honours `mode=0`
# (Detect-equivalent, default 320x200) and a "smuggle dimensions"
# extension via mode = width*1000+height; the named modes here are
# accepted-but-ignored. Real TP7 values:
vgalo: int32 = int32(0)     # 640x200, 16 colours, 4 pages
vgamed: int32 = int32(1)    # 640x350, 16 colours, 2 pages
vgahi: int32 = int32(2)     # 640x480, 16 colours, 1 page
egalo: int32 = int32(0)
egahi: int32 = int32(1)
cgac0: int32 = int32(0)
cgac1: int32 = int32(1)
cgac2: int32 = int32(2)
cgac3: int32 = int32(3)
cgahi: int32 = int32(4)

# TP7 BGI putimage operators: how an incoming bitmap is combined
# with the existing canvas pixel. NormalPut copies, the others
# blend.
normalput: int32 = int32(0)
copyput: int32 = int32(0)
xorput: int32 = int32(1)
orput: int32 = int32(2)
andput: int32 = int32(3)
notput: int32 = int32(4)

# TP7 BGI 16-color palette names. Programs say `setcolor(LightBlue)`
# rather than `setcolor(9)`; without these constants the source
# fails to resolve. Values match the palette index used by
# `_resolve_color`.
black: int32 = int32(0)
blue: int32 = int32(1)
green: int32 = int32(2)
cyan: int32 = int32(3)
red: int32 = int32(4)
magenta: int32 = int32(5)
brown: int32 = int32(6)
lightgray: int32 = int32(7)
darkgray: int32 = int32(8)
lightblue: int32 = int32(9)
lightgreen: int32 = int32(10)
lightcyan: int32 = int32(11)
lightred: int32 = int32(12)
lightmagenta: int32 = int32(13)
yellow: int32 = int32(14)
white: int32 = int32(15)


# TP7 16-color palette -> 0xRRGGBB packed. We drop the alpha
# channel (int32 fits 24-bit RGB but not 32-bit ARGB); when an
# SDL display layer ships, the present-loop synthesises alpha
# (=0xFF) at upload time.
_PALETTE: list[int32] = [
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

    width: int32
    height: int32
    fg: int32        # current pen palette index (0..15)
    bg: int32        # current background palette index
    pen_x: int32     # last drawn point (for `lineto`/`moveto` in Tier B)
    pen_y: int32
    pixels: list[int32]  # length = width * height, packed AARRGGBB

    def __init__(self) -> None:
        self.width = int32(0)
        self.height = int32(0)
        self.fg = int32(15)
        self.bg = int32(0)
        self.pen_x = int32(0)
        self.pen_y = int32(0)
        self.pixels = []

    def is_open(self) -> bool:
        return self.width > 0 and self.height > 0


# Module-level singleton. Pascal source sees the procedure-style
# API; this object is internal.
_ctx: GraphContext = GraphContext()


class pointtype:
    """TP7 BGI `PointType = record x, y: integer end`. Used by
    `fillpoly` and as the element type of point arrays in user
    programs. The Pascal frontend lowers `(x: 1; y: 2)` record
    literals to `pointtype(1, 2)` ctor calls -- a 2-arg
    constructor lets array-of-pointtype typed-consts initialise
    inline rather than going through a build-then-assign loop."""

    x: int32
    y: int32

    def __init__(self, x: int32 = int32(0), y: int32 = int32(0)) -> None:
        self.x = x
        self.y = y


class BgiImage:
    """TP7 BGI image buffer: a copy of a canvas rectangle, used by
    `getimage` / `putimage` for sprite-style save/restore. Real
    BGI returned a heap pointer wrapping a driver-specific packed
    format; we just hold width / height plus a list of palette-
    index pixels. The Pascal frontend aliases the bare `pointer`
    type to this class -- programs in the legacy corpus only use
    `pointer` for getimage/putimage buffers."""

    width: int32
    height: int32
    pixels: list[int32]

    def __init__(self) -> None:
        self.width = int32(0)
        self.height = int32(0)
        self.pixels = []


def _resolve_color(c: int32) -> int32:
    """Map a TP7 palette index (0..15) to packed AARRGGBB. Indices
    outside the range default to white -- TP7 itself is lenient
    about out-of-range color values."""
    if c < 0 or c >= 16:
        return _PALETTE[15]
    return _PALETTE[c]


def _set_pixel_raw(x: int32, y: int32, packed: int32) -> None:
    """Write a packed color to (x, y) if the coordinates are inside
    the canvas. Out-of-range writes are silently dropped -- BGI's
    `PutPixel` behaves the same."""
    if x < 0 or x >= _ctx.width:
        return
    if y < 0 or y >= _ctx.height:
        return
    _ctx.pixels[y * _ctx.width + x] = packed


def initgraph(driver: int32, mode: int32, path: str) -> None:
    """Open a graphics canvas. The TP7 `driver` / `mode` /
    `pathtodriver` args are accepted for source-language fidelity.
    The standard TP7 mode constants (vgalo / vgamed / vgahi /
    egalo / egahi / cgahi etc.) are small integers in the 0..10
    range; we map them to their real BGI dimensions. A `mode >=
    1001` is interpreted as a width*1000 + height smuggle, a POC
    convenience extension for programs that want an unusual canvas
    size."""
    if mode >= 1001:
        width = mode // 1000
        height = mode % 1000
    elif driver == 9 and mode == 0:        # VGA + VGALo
        width = 640
        height = 200
    elif driver == 9 and mode == 1:        # VGA + VGAMed
        width = 640
        height = 350
    elif driver == 9 and mode == 2:        # VGA + VGAHi
        width = 640
        height = 480
    elif driver == 3 and mode == 0:        # EGA + EGALo
        width = 640
        height = 200
    elif driver == 3 and mode == 1:        # EGA + EGAHi
        width = 640
        height = 350
    elif driver == 1 and mode == 4:        # CGA + CGAHi
        width = 640
        height = 200
    elif driver == 1:                      # other CGA modes
        width = 320
        height = 200
    else:
        # Detect (driver=0), unknown driver/mode, or any other
        # combination. Legacy TP7 demos used `gd := Detect; gm :=
        # Detect;` -- the BGI picked whatever the card supported.
        # On a typical CGA machine that was CGAHi (640x200); on
        # EGA/VGA the chosen mode was taller, but classic kid-
        # program coordinates rarely went past y=200 so the image
        # ended up crammed in the upper part of a tall window.
        # 640x200 is the lowest common denominator that still fits
        # the corpus of legacy programs the POC targets. Programs
        # that want a different size use the smuggle (mode = w*1000
        # + h) or pass an explicit driver+mode pair.
        width = 640
        height = 200
    _ctx.width = int32(width)
    _ctx.height = int32(height)
    _ctx.fg = int32(15)
    _ctx.bg = int32(0)
    _ctx.pen_x = int32(0)
    _ctx.pen_y = int32(0)
    bg_packed = _resolve_color(_ctx.bg)
    _ctx.pixels = [bg_packed] * (width * height)


def closegraph() -> None:
    """Flush the canvas to `out.ppm` (ASCII P3 format) in the
    program's cwd. The file is written even when the canvas is
    empty so the user always finds out where the snapshot landed.
    State is reset afterwards so `initgraph` can be called again."""
    if _ctx.is_open():
        _write_ppm("out.ppm")
    _ctx.width = int32(0)
    _ctx.height = int32(0)
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
    i = int32(0)
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


def setcolor(c: int32) -> None:
    _ctx.fg = c


def setbkcolor(c: int32) -> None:
    _ctx.bg = c


def cleardevice() -> None:
    """Fill the entire canvas with the current background color."""
    bg_packed = _resolve_color(_ctx.bg)
    i = int32(0)
    n = _ctx.width * _ctx.height
    while i < n:
        _ctx.pixels[i] = bg_packed
        i += 1


def clearviewport() -> None:
    """TP7 BGI: fill the active viewport with the background color.
    The POC has no viewport concept (the whole canvas is the
    viewport), so this is equivalent to `cleardevice`."""
    cleardevice()


def setfillstyle(pattern: int32, color: int32) -> None:
    """TP7 BGI fill style. The POC only supports solid fill, so the
    pattern argument is accepted for source-compatibility but
    ignored. The color updates the foreground pen so subsequent
    `bar` / `fillellipse` / `floodfill` calls use it."""
    _ctx.fg = color


def settextstyle(font: int32, direction: int32, size: int32) -> None:
    """TP7 BGI font selection. The POC ships only the 8x8 bitmap
    font with left-to-right horizontal direction, so font /
    direction / size are accepted for source-compatibility but
    ignored."""
    pass


def setlinestyle(line_style: int32, pattern: int32,
                 thickness: int32) -> None:
    """TP7 BGI line style: SolidLn / DottedLn / CenterLn / DashedLn
    / UserBitLn, optional 16-bit user pattern, NormWidth (1) or
    ThickWidth (3). The POC always draws solid 1-pixel lines, so
    these are accepted for source-compatibility but ignored."""
    pass


def setgraphmode(mode: int32) -> None:
    """TP7 BGI: switch graphics modes while a session is open. The
    POC keeps the canvas dimensions chosen at `initgraph` and does
    not honour mid-program mode switches; the call is accepted for
    source-compatibility but ignored. Programs that need a
    different canvas size should pass the desired mode to
    `initgraph` directly (or use the width*1000+height smuggle)."""
    pass


def setfillpattern(pattern: Array[int32, 8], color: int32) -> None:
    """TP7 BGI custom fill pattern (`FillPatternType` is an 8-byte
    bitmask). The POC only supports solid fill, so we set the
    foreground colour and ignore the pattern -- subsequent
    `bar` / `fillpoly` / `floodfill` calls draw in solid `color`.
    The pattern parameter is typed `Array[int32, 8]` so that
    typed-const `fillpatterntype` values (which the Pascal frontend
    expands to `Array[int32, 8]`) pass without an explicit copy."""
    _ctx.fg = color


def drawpoly(num_points: int32, points: Span[pointtype]) -> None:
    """TP7 BGI: draw the outline of a closed polygon defined by
    `num_points` vertices. Connects consecutive points with
    straight lines in the current foreground colour and auto-
    closes the polygon (last vertex back to the first)."""
    if num_points < 2:
        return
    i = int32(0)
    while i < num_points - 1:
        line(points[i].x, points[i].y,
             points[i + 1].x, points[i + 1].y)
        i += 1
    line(points[num_points - 1].x, points[num_points - 1].y,
         points[0].x, points[0].y)


def fillpoly(num_points: int32, points: Span[pointtype]) -> None:
    """TP7 BGI: fill a closed polygon defined by `num_points`
    vertices. Uses a scan-line fill: for each canvas row that
    intersects the polygon's bounding box, find every edge
    crossing and fill the spans between paired crossings.
    Coincident-vertex edges contribute one crossing each so
    horizontal edges don't double-count. The polygon is auto-
    closed (last vertex connects back to the first)."""
    if num_points < 3:
        return
    packed = _resolve_color(_ctx.fg)
    # Bounding-box scan range (clip to canvas).
    min_y = points[0].y
    max_y = points[0].y
    i = int32(1)
    while i < num_points:
        if points[i].y < min_y:
            min_y = points[i].y
        if points[i].y > max_y:
            max_y = points[i].y
        i += 1
    if min_y < 0:
        min_y = int32(0)
    if max_y >= _ctx.height:
        max_y = _ctx.height - 1
    # Scan each row in the bounding box.
    y = min_y
    while y <= max_y:
        crossings: list[int32] = []
        j = int32(0)
        while j < num_points:
            k = (j + 1) % num_points
            y0 = points[j].y
            y1 = points[k].y
            x0 = points[j].x
            x1 = points[k].x
            # Edge crosses scan line `y` iff y is in [min(y0,y1),
            # max(y0,y1)). The half-open interval drops the upper
            # endpoint so a shared vertex between two edges
            # contributes exactly one crossing.
            cross_lo = y0 if y0 < y1 else y1
            cross_hi = y0 if y0 > y1 else y1
            if y >= cross_lo and y < cross_hi:
                # Linear interpolation: x at this y.
                xi = x0 + (y - y0) * (x1 - x0) // (y1 - y0)
                crossings.append(xi)
            j += 1
        # Sort crossings ascending (simple insertion sort -- the
        # crossings list is short).
        a = int32(1)
        while a < len(crossings):
            v = crossings[a]
            b = a - 1
            while b >= 0 and crossings[b] > v:
                crossings[b + 1] = crossings[b]
                b -= 1
            crossings[b + 1] = v
            a += 1
        # Fill spans between pairs of crossings.
        p = int32(0)
        while p + 1 < len(crossings):
            x_lo = crossings[p]
            x_hi = crossings[p + 1]
            if x_lo < 0:
                x_lo = int32(0)
            if x_hi >= _ctx.width:
                x_hi = _ctx.width - 1
            x = x_lo
            while x <= x_hi:
                _set_pixel_raw(x, y, packed)
                x += 1
            p += 2
        y += 1


def imagesize(x1: int32, y1: int32, x2: int32, y2: int32) -> int32:
    """TP7 BGI: byte count needed to store the canvas rectangle
    via `getimage`. Real BGI used this to size a heap allocation;
    `BgiImage` self-allocates so the return value is opaque to
    the runtime, but we still return a plausible 4-bytes-per-pixel
    count plus a small header so programs that range-check it
    against their `wielk: word` variable don't overflow."""
    w = x2 - x1 + 1
    h = y2 - y1 + 1
    if w < 0:
        w = -w
    if h < 0:
        h = -h
    return int32(4) + int32(4) * w * h


def getmem(buf: BgiImage, size: int32) -> None:
    """TP7 `GetMem(p, size)`: real BGI allocated `size` bytes and
    set `p^`. The POC's `BgiImage` self-allocates via its default
    constructor, so this is a no-op (the var-param semantics
    aren't needed; the buffer's pixels list is repopulated on the
    next `getimage` call)."""
    pass


def freemem(buf: BgiImage, size: int32) -> None:
    """TP7 `FreeMem(p, size)`: TPy's garbage collector handles the
    buffer's lifetime, so this is a no-op. The buffer's internal
    pixel list is left in place; the next `getimage` overwrites
    it. Calling `freemem` then using `buf` again is benign here
    even though it would have been undefined in real TP7."""
    pass


def getimage(x1: int32, y1: int32, x2: int32, y2: int32,
             buf: BgiImage) -> None:
    """Snapshot canvas pixels in the inclusive rect [(x1,y1),
    (x2,y2)] into `buf`. Out-of-range pixels read as black (palette
    index 0) so the snapshot stays rectangular even when the rect
    is partly off-canvas."""
    w = x2 - x1 + 1
    h = y2 - y1 + 1
    if w <= 0 or h <= 0:
        buf.width = int32(0)
        buf.height = int32(0)
        buf.pixels = []
        return
    buf.width = w
    buf.height = h
    buf.pixels = [int32(0)] * (w * h)
    y = int32(0)
    while y < h:
        sy = y1 + y
        x = int32(0)
        while x < w:
            sx = x1 + x
            if (sx >= 0 and sx < _ctx.width
                    and sy >= 0 and sy < _ctx.height):
                buf.pixels[y * w + x] = _ctx.pixels[sy * _ctx.width + sx]
            x += 1
        y += 1


def putimage(x: int32, y: int32, buf: BgiImage, op: int32) -> None:
    """Blit `buf` at canvas (x, y). `op` is TP7's BitBlt operator:
    0 = NormalPut (copy), 1 = XorPut, 2 = OrPut, 3 = AndPut,
    4 = NotPut. The op blends `buf`'s packed colour with the
    existing canvas pixel; the result is searched back into the
    16-colour palette via `_resolve_color` so subsequent reads
    via `getpixel` round-trip correctly."""
    if buf.width <= 0 or buf.height <= 0:
        return
    j = int32(0)
    while j < buf.height:
        ty = y + j
        i = int32(0)
        while i < buf.width:
            tx = x + i
            if (tx >= 0 and tx < _ctx.width
                    and ty >= 0 and ty < _ctx.height):
                src = buf.pixels[j * buf.width + i]
                idx = ty * _ctx.width + tx
                dst = _ctx.pixels[idx]
                if op == 0:           # NormalPut / CopyPut
                    out = src
                elif op == 1:         # XorPut
                    out = src ^ dst
                elif op == 2:         # OrPut
                    out = src | dst
                elif op == 3:         # AndPut
                    out = src & dst
                elif op == 4:         # NotPut
                    out = src ^ int32(0xFFFFFF)
                else:
                    out = src
                _ctx.pixels[idx] = out
            i += 1
        j += 1


def setactivepage(page: int32) -> None:
    """TP7 BGI page flipping. Multi-page video modes were a TP7
    feature for double-buffered animation on 16-color cards; the
    POC has a single canvas, so this is a no-op. Programs that
    relied on `setactivepage` + `setvisualpage` for flicker-free
    animation render directly to the canvas instead."""
    pass


def setvisualpage(page: int32) -> None:
    """TP7 BGI page flipping (visual half). No-op for the POC --
    see `setactivepage`."""
    pass


def putpixel(x: int32, y: int32, c: int32) -> None:
    _set_pixel_raw(x, y, _resolve_color(c))


def getpixel(x: int32, y: int32) -> int32:
    """Return the palette index of the pixel at (x, y). Out-of-
    range reads return 0 (black). The implementation scans the
    palette for an exact RGBA match; TP7 BGI does the same since
    `PutPixel` always writes a palette color."""
    if x < 0 or x >= _ctx.width:
        return int32(0)
    if y < 0 or y >= _ctx.height:
        return int32(0)
    packed = _ctx.pixels[y * _ctx.width + x]
    i = int32(0)
    while i < 16:
        if _PALETTE[i] == packed:
            return i
        i += 1
    return int32(0)


def line(x1: int32, y1: int32, x2: int32, y2: int32) -> None:
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
    sx = int32(1) if x1 < x2 else int32(-1)
    sy = int32(1) if y1 < y2 else int32(-1)
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


def rectangle(x1: int32, y1: int32, x2: int32, y2: int32) -> None:
    """Outline of an axis-aligned rectangle. Lower-right corner is
    inclusive (matches BGI). Caller is responsible for any
    coordinate normalisation -- a swapped pair still draws but in
    a non-canonical traversal order."""
    line(x1, y1, x2, y1)
    line(x2, y1, x2, y2)
    line(x2, y2, x1, y2)
    line(x1, y2, x1, y1)


def bar(x1: int32, y1: int32, x2: int32, y2: int32) -> None:
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


def circle(cx: int32, cy: int32, r: int32) -> None:
    """Midpoint circle algorithm. Plots eight symmetric octants
    per step so the outline is connected even at low radii."""
    packed = _resolve_color(_ctx.fg)
    x = int32(0)
    y = r
    d = int32(3) - 2 * r
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


def getmaxx() -> int32:
    """Highest valid x coordinate (width - 1). TP7 BGI convention."""
    return _ctx.width - 1


def getmaxy() -> int32:
    return _ctx.height - 1


# Pen state (Tier B) -----------------------------------------------
#
# TP7 BGI's `MoveTo` / `LineTo` form a "turtle graphics" pattern --
# the current pen position is mutated by `MoveTo` and read by
# `LineTo` (which then advances to the destination). `Line(...)`
# itself does NOT move the pen; only `LineTo` does.


def moveto(x: int32, y: int32) -> None:
    _ctx.pen_x = x
    _ctx.pen_y = y


def lineto(x: int32, y: int32) -> None:
    """Draw a line from the current pen position to (x, y), then
    advance the pen there."""
    line(_ctx.pen_x, _ctx.pen_y, x, y)
    _ctx.pen_x = x
    _ctx.pen_y = y


def getx() -> int32:
    return _ctx.pen_x


def gety() -> int32:
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

_FONT_FIRST: int32 = int32(32)
_FONT_LAST: int32 = int32(95)  # exclusive (' ' through '_')
_FONT_BYTES_PER_CHAR: int32 = int32(8)

_FONT: list[int32] = [
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


def _glyph_offset(c: int32) -> int32:
    """Byte offset of the glyph for code `c` in `_FONT`, or -1 if
    out of range. Lowercase ASCII folds to uppercase so labels
    typed in either case render."""
    code = c
    if code >= int32(97) and code <= int32(122):
        code = code - int32(32)  # 'a'..'z' -> 'A'..'Z'
    if code < _FONT_FIRST or code >= _FONT_LAST:
        return int32(-1)
    return (code - _FONT_FIRST) * _FONT_BYTES_PER_CHAR


def _draw_glyph(x: int32, y: int32, c: int32,
                packed: int32) -> None:
    """Plot a single 8x8 glyph at (x, y). Each set bit in the
    glyph's row bytes becomes a pixel in the current pen color."""
    offset = _glyph_offset(c)
    if offset < 0:
        return
    row = int32(0)
    while row < int32(8):
        bits = _FONT[offset + row]
        col = int32(0)
        while col < int32(8):
            mask = int32(1) << (int32(7) - col)
            if (bits & mask) != 0:
                _set_pixel_raw(x + col, y + row, packed)
            col += 1
        row += 1


def outtextxy(x: int32, y: int32, s: str) -> None:
    """Draw a string at (x, y) using the embedded 8x8 bitmap font
    in the current pen color. Each character advances the
    horizontal cursor by 8 pixels; unsupported characters render
    as a blank cell. Does NOT update the BGI pen position --
    matches TP7's behaviour."""
    packed = _resolve_color(_ctx.fg)
    cx = x
    i = int32(0)
    n = len(s)
    while i < n:
        _draw_glyph(cx, y, int32(ord(s[i])), packed)
        cx += int32(8)
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


def ellipse(cx: int32, cy: int32,
            start_angle: int32, end_angle: int32,
            rx: int32, ry: int32) -> None:
    """Outline of an axis-aligned elliptical arc centred at
    (cx, cy) with horizontal radius `rx` and vertical radius `ry`,
    swept from `start_angle` to `end_angle` (degrees, CCW from the
    +X axis -- TP7 BGI convention). 0..360 draws the full ellipse.
    The angle parameters are accepted for source-compatibility with
    TP7 but currently round to "full ellipse" -- partial-arc
    ellipses can land alongside arc-with-angle support."""
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
    x = int32(0)
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


def arc(cx: int32, cy: int32, start_angle: int32,
        end_angle: int32, r: int32) -> None:
    """Draw a circular arc from `start_angle` to `end_angle` (both
    in degrees, BGI convention: 0 = east, increasing
    counter-clockwise) at radius `r`. Implemented parametrically:
    walk the angle in 1-degree steps and plot each (x, y) via the
    polar-to-cartesian transform. Coarser than a midpoint variant
    but accurate enough for the canvas sizes the POC targets and
    independent of the radius."""
    packed = _resolve_color(_ctx.fg)
    if r <= 0:
        _set_pixel_raw(cx, cy, packed)
        return
    a = start_angle
    end = end_angle
    if end < a:
        end = end + int32(360)
    while a <= end:
        rad = float(a) * 3.141592653589793 / 180.0
        x = cx + int32(round(float(r) * _math.cos(rad)))
        # BGI's y axis is screen-style (y grows downward) but
        # angle 0 is east + counter-clockwise; cos handles x as
        # usual, and we flip the sin component so a +90 angle
        # lands above the centre, matching what TP7 users see.
        y = cy - int32(round(float(r) * _math.sin(rad)))
        _set_pixel_raw(x, y, packed)
        a += 1


def fillellipse(cx: int32, cy: int32, rx: int32, ry: int32) -> None:
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
        ext = int32(0)
        while (ext + 1) * (ext + 1) * ry_sq <= num:
            ext += 1
        x = cx - ext
        x_end = cx + ext
        y = cy + dy
        while x <= x_end:
            _set_pixel_raw(x, y, packed)
            x += 1
        dy += 1


# Tier C primitives ------------------------------------------------
#
# Less-common BGI procedures that complete the unit's surface
# without major new infrastructure. None of these need a separate
# milestone -- they all sit on top of the pixel buffer and
# palette table.


def floodfill(x: int32, y: int32, border: int32) -> None:
    """4-way flood fill from (x, y). Recolors every pixel reachable
    from the seed that ISN'T the border color, using the current
    pen color as the fill. An empty seed (seed already equals the
    border color or is outside the canvas) is a no-op. Iterative
    via a worklist -- TPy doesn't love deep recursion and the
    typical kid-program canvas can easily exceed the call stack."""
    if x < 0 or x >= _ctx.width:
        return
    if y < 0 or y >= _ctx.height:
        return
    fill_packed = _resolve_color(_ctx.fg)
    border_packed = _resolve_color(border)
    start_idx = y * _ctx.width + x
    if _ctx.pixels[start_idx] == border_packed:
        return
    if _ctx.pixels[start_idx] == fill_packed:
        return
    target = _ctx.pixels[start_idx]
    stack_x: list[int32] = [x]
    stack_y: list[int32] = [y]
    while len(stack_x) > 0:
        cx = stack_x.pop()
        cy = stack_y.pop()
        if cx < 0 or cx >= _ctx.width:
            continue
        if cy < 0 or cy >= _ctx.height:
            continue
        idx = cy * _ctx.width + cx
        if _ctx.pixels[idx] != target:
            continue
        _ctx.pixels[idx] = fill_packed
        stack_x.append(cx + int32(1))
        stack_y.append(cy)
        stack_x.append(cx - int32(1))
        stack_y.append(cy)
        stack_x.append(cx)
        stack_y.append(cy + int32(1))
        stack_x.append(cx)
        stack_y.append(cy - int32(1))


def bar3d(x1: int32, y1: int32, x2: int32, y2: int32,
          depth: int32, top: bool) -> None:
    """`Bar3D` -- a filled rectangle plus depth lines on the
    right + top edges, giving the chart-style 3D look. `top=True`
    draws the top face's near edge as well; `top=False` leaves it
    open (used when stacking bars vertically). The body fill uses
    the current pen color; the 3D edge lines use the same color."""
    bar(x1, y1, x2, y2)
    if depth <= 0:
        return
    # Right face: depth-shifted vertical edge from (x2+depth, y1-?)
    # In BGI the depth lines slant up-and-right by `depth` pixels.
    rx = x2 + depth
    ty = y1 - depth
    by = y2 - depth
    line(x2, y1, rx, ty)
    line(x2, y2, rx, by)
    line(rx, ty, rx, by)
    if top:
        line(x1, y1, x1 + depth, ty)
        line(x1 + depth, ty, rx, ty)


def setrgbpalette(index: int32, r: int32, g: int32,
                   b: int32) -> None:
    """Override one entry in the 16-color palette. TP7 uses 6-bit
    RGB values (0..63) here; we accept either 6-bit or 8-bit
    values transparently -- values <= 63 get scaled up to 0..255
    (multiply by 4 and saturate), values > 63 are taken as 8-bit
    directly. Out-of-range `index` is silently ignored."""
    if index < 0 or index >= 16:
        return
    rr = r if r > 63 else (r * 4 if r * 4 < 256 else 255)
    gg = g if g > 63 else (g * 4 if g * 4 < 256 else 255)
    bb = b if b > 63 else (b * 4 if b * 4 < 256 else 255)
    _PALETTE[index] = (rr << 16) | (gg << 8) | bb


def detectgraph(var_driver: int32, var_mode: int32) -> None:
    """No-op stub. TP7's `DetectGraph(var Driver, Mode: Integer)`
    asks the BGI driver to suggest a (driver, mode) pair; we
    operate independently of BGI drivers so the call doesn't
    need to do anything. Programs that use the standard idiom of
    calling `DetectGraph` immediately before `InitGraph` work --
    `InitGraph(0, 0, '')` already picks our default canvas."""
    pass


def registerbgidriver(driver: int32) -> int32:
    """No-op stub matching TP7's `RegisterBGIDriver` signature.
    Real TP7 used this to register linked-in driver binaries
    when running off-disk; we don't load drivers, so the call
    always succeeds with status 0."""
    return int32(0)


def registerbgifont(font: int32) -> int32:
    """No-op stub matching TP7's `RegisterBGIFont` signature.
    Our text rendering uses the embedded 8x8 font set unconditionally."""
    return int32(0)


"""TPy-side implementation of Turbo Pascal's `Crt` unit, portable
subset. Imported via Pascal `uses Crt;`, which lowers to a star
import of bare `crt` (the file's stem). The visible surface is
the union of:

  - 16 color constants (`black`, `blue`, ..., `white`) -- lowercase
    so they match what the Pascal lexer canonicalises identifiers
    to.
  - Foreground / background color control (`textcolor`,
    `textbackground`) via ANSI SGR escapes.
  - Screen / cursor (`clrscr`, `cleol`, `gotoxy`) via ANSI CSI.
  - Timing (`delay`, `sound`, `nosound`). `delay` calls
    `time.sleep`; `sound` and `nosound` are no-ops on modern
    systems (TP7's PC-speaker calls don't translate).
  - `readkey` -- blocking one-char read via TPy's `input()`
    builtin. Returns `char('\\r')` when the line is empty (matches
    TP7's "Enter alone" behaviour); the rest of the line is
    discarded.

Deferred (not in M14): `wherex` / `wherey` (need a cursor-position
round trip), `keypressed` (no non-blocking stdin available without
termios, which TPy can't model yet).

ANSI codes used:
  CSI = "\\x1b["
  CSI + "<n>m"       -- SGR (color attrs)
  CSI + "2J" + "H"   -- clear screen + home
  CSI + "K"          -- clear to end of line
  CSI + "<y>;<x>H"   -- cursor position (1-based)

Pascal source addresses these by their Pascal name (case-
insensitive). The translator lowers identifiers to lowercase, so
the symbols here are lowercase and underscore-free to match. Some
Pascal names with no underscore would otherwise stay unchanged
(e.g. `clrscr` rather than `clr_scr`), which is what the user
writes anyway.
"""

from __future__ import annotations

import time as _time

from tpy import char, int32


# Standard TP7 color palette (0-15). Background colors share the
# same numeric range; SGR encoding selects FG (30+c) or BG (40+c).
black: int32 = 0
blue: int32 = 1
green: int32 = 2
cyan: int32 = 3
red: int32 = 4
magenta: int32 = 5
brown: int32 = 6
lightgray: int32 = 7
darkgray: int32 = 8
lightblue: int32 = 9
lightgreen: int32 = 10
lightcyan: int32 = 11
lightred: int32 = 12
lightmagenta: int32 = 13
yellow: int32 = 14
white: int32 = 15


# TP7 color indices don't line up with ANSI SGR indices: TP7 has
# blue=1, green=2, cyan=3, red=4 (R/G/B in least-significant-bit
# order) while ANSI is red=1, green=2, yellow=3, blue=4. The
# `_ANSI_FG`/`_ANSI_BG` tables hold the SGR code per TP7 color
# (indices 0..15, with the bright variants in the upper half).
_ANSI_FG: list[int32] = [
    30, 34, 32, 36, 31, 35, 33, 37,
    90, 94, 92, 96, 91, 95, 93, 97,
]
_ANSI_BG: list[int32] = [
    40, 44, 42, 46, 41, 45, 43, 47,
    100, 104, 102, 106, 101, 105, 103, 107,
]


def textcolor(c: int32) -> None:
    print("\x1b[" + str(_ANSI_FG[c & 15]) + "m", end="")


def textbackground(c: int32) -> None:
    print("\x1b[" + str(_ANSI_BG[c & 15]) + "m", end="")


def clrscr() -> None:
    # Clear screen + cursor home.
    print("\x1b[2J\x1b[H", end="")


def clreol() -> None:
    print("\x1b[K", end="")


def gotoxy(x: int32, y: int32) -> None:
    # TP7 GotoXY is 1-based and column-first; ANSI CSI H is
    # row;col (also 1-based) so we just swap and pass through.
    print("\x1b[" + str(y) + ";" + str(x) + "H", end="")


def delay(ms: int32) -> None:
    # TP7 measures in milliseconds; `time.sleep` takes seconds.
    _time.sleep(ms / 1000.0)


def sound(hz: int32) -> None:
    # PC-speaker tone -- not available on modern systems.
    pass


def nosound() -> None:
    pass


def keypressed() -> bool:
    """TP7 `KeyPressed`: True if a key is waiting in the keyboard
    buffer. Real TP7 polls the BIOS keyboard state; the POC has no
    non-blocking stdin probe, so we always return True. The common
    idiom `repeat ... until KeyPressed;` thus exits its loop on the
    first iteration -- programs that relied on the loop running for
    a while will need explicit `delay()` calls between checks. The
    SDL display hook (when on) provides the user-visible "block
    until key" via the show-blocking call at CloseGraph."""
    return True


def readkey() -> char:
    # TP7's ReadKey returns one character without echo. Without
    # raw-mode termios (unavailable in TPy), the closest portable
    # behaviour is "read one line via `input`, return the first
    # character". An empty line stands in for "user pressed Enter".
    # On stdin EOF (piped input drained) TP7 would block forever; we
    # return CR so the program can detect end-of-input via the loop
    # condition rather than panic.
    try:
        line: str = input()
    except ValueError:
        # TPy's input() raises ValueError on stdin EOF (no dedicated
        # EOFError class yet -- see runtime/cpp/include/tpy/builtins.hpp).
        return char("\r")
    if len(line) == 0:
        return char("\r")
    return line[0]

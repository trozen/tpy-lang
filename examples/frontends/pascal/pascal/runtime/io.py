"""Pascal I/O runtime: write / writeln / read / readln.

M1: `writeln(StrView)`. M2: `writeln_int(Int32)`. Float / boolean /
char overloads, the `write` (no-newline) variants, and `read` /
`readln` for stdin arrive in later milestones.

Each Pascal arg type gets a distinct TPy function (`writeln_int`,
`writeln_float`, ...) instead of an `@overload`, so the translator's
overload-dispatch can stay at the import-name level rather than relying
on TPy's overload resolution -- this is also what the design table
implies (one runtime entry per overload).
"""

from tpy import Int32


def writeln(s: str) -> None:
    print(s)


def writeln_int(n: Int32) -> None:
    print(n)

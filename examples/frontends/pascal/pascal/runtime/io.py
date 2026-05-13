"""Pascal I/O runtime: write / writeln / read / readln.

Per-overload runtime entries (writeln_int, writeln_float, readln_int,
...) rather than `@overload`, so the translator's dispatch stays at the
import-name level (matches the design table's "one runtime entry per
overload" guidance).

M1: `writeln(StrView)`. M2: `writeln_int(Int32)`. M8: `readln_int` /
`readln_line` for stdin -- both route through TPy's `input()` builtin
(added with M8) so the Pascal runtime stays in pure TPy with no
vendored C/C++ source. Float / boolean / char overloads and `write`
(no-newline) variants land in later milestones as needed.
"""

from tpy import Int32


def writeln(s: str) -> None:
    print(s)


def writeln_int(n: Int32) -> None:
    print(n)


def readln_int() -> Int32:
    """Read a line of stdin and parse it as an Int32. Pascal's
    `readln(int_var)` lowers to `int_var := readln_int();`."""
    return Int32(input())


def readln_line() -> str:
    """Read a line of stdin and return it. The Pascal translator
    routes `readln(str_var)` through `str_var.assign(readln_line())`
    so the lvalue keeps its identity."""
    return input()

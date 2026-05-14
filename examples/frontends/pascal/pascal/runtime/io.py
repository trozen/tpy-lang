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


def writeln_float(x: float) -> None:
    print(x)


# `write` (no trailing newline). Pascal lets writeln(...) and write(...)
# stream a sequence of values; M9 keeps it simple -- per-type calls
# with no separator, matching the way the translator routes one arg
# per builtin call.
def write(s: str) -> None:
    print(s, end="")


def write_int(n: Int32) -> None:
    print(n, end="")


def write_float(x: float) -> None:
    print(x, end="")


def readln_int() -> Int32:
    """Read a line of stdin and parse it as an Int32. Pascal's
    `readln(int_var)` lowers to `int_var := readln_int();`."""
    return Int32(input())


def readln_line() -> str:
    """Read a line of stdin and return it. The Pascal translator
    routes `readln(str_var)` through `str_var.assign(readln_line())`
    so the lvalue keeps its identity."""
    return input()


class TextFile:
    """Pascal text-file I/O backed by an in-memory line buffer.

    Pascal's two-step open dance (`Assign(f, name)` then
    `Reset(f)` / `Rewrite(f)`) requires the file value to hold an
    unopened state, which doesn't sit naturally on top of TPy's
    `open(...)` result. M13 sidesteps the problem by deferring the
    handle: `reset()` slurps the file into `_lines` and serves
    `readln_*` from a per-line cursor; `rewrite()` accumulates writes
    in `_write_buf` and flushes at `close()`. The performance cost is
    fine for the kid-program file sizes TP7 programs deal with.

    The translator rewrites Pascal-spelt `Assign(f, n)`, `Reset(f)`,
    `Rewrite(f)`, `Close(f)`, `Eof(f)`, `Writeln(f, x)`, `Readln(f, x)`
    to method calls on this class.
    """

    path: str
    _lines: list[str]
    _index: Int32
    _write_buf: list[str]

    def __init__(self) -> None:
        self.path = ""
        self._lines = []
        self._index = Int32(0)
        self._write_buf = []

    def assign(self, name: str) -> None:
        self.path = name

    def reset(self) -> None:
        """Open the assigned path for reading. Slurps every line into
        an in-memory buffer; subsequent `readln_*` calls draw from
        that buffer and `eof` reports cursor-past-end."""
        with open(self.path, "r") as f:
            data = f.read()
        self._lines = data.splitlines()
        self._index = Int32(0)
        self._write_buf = []

    def rewrite(self) -> None:
        """Open the assigned path for writing. Pascal `Rewrite`
        truncates an existing file; we accumulate writes into a
        per-line buffer and flush at `close()`."""
        self._lines = []
        self._index = Int32(0)
        self._write_buf = []

    def close(self) -> None:
        """Flush pending writes (if any) and reset the file state.
        Calling `close` twice or on an unopened file is a no-op."""
        if len(self._write_buf) > 0:
            with open(self.path, "w") as f:
                for chunk in self._write_buf:
                    f.write(chunk)
        self._lines = []
        self._index = Int32(0)
        self._write_buf = []

    def eof(self) -> bool:
        return self._index >= len(self._lines)

    def readln_line(self) -> str:
        line = self._lines[self._index]
        self._index += 1
        return line

    def readln_int(self) -> Int32:
        return Int32(self.readln_line())

    def writeln_str(self, s: str) -> None:
        self._write_buf.append(s + "\n")

    def writeln_int(self, n: Int32) -> None:
        self._write_buf.append(str(n) + "\n")

    def writeln_float(self, x: float) -> None:
        self._write_buf.append(str(x) + "\n")

    def write_str(self, s: str) -> None:
        self._write_buf.append(s)

    def write_int(self, n: Int32) -> None:
        self._write_buf.append(str(n))

    def write_float(self, x: float) -> None:
        self._write_buf.append(str(x))

# tpy: ext_module
# Default parameter values and keyword-only / positional-only params at the
# @export boundary: the wrapper's PyArg format carries `|` for the optional run,
# `$` for the keyword-only run, and an empty kwlist entry per positional-only
# param, so an omitted slot binds the default instead of dropping trailing args
# -- the case a Python caller creates by skipping an EARLIER defaulted param
# while passing a later one by keyword. Covers a default of every marshallable
# by-value shape (int/float/bool/str/bytes/tuple/enum/Final constant), a
# REQUIRED keyword-only param (which PyArg cannot express, so the glue
# NULL-checks it), and defaults on a class __init__ and method.
from enum import IntEnum
from typing import Final

from tpy import Int32, Int64
from tpy.extern import export

STEP: Final[Int64] = 7
ORIGIN: Final[tuple[Int32, Int32]] = (10, 20)


@export
class Color(IntEnum):
    RED = 0
    BLUE = 1


@export
def greet(name: str, greeting: str = "Hello", *, excited: bool = False) -> str:
    s = greeting + ", " + name
    if excited:
        s = s + "!"
    return s


@export
def advance(n: Int64, by: Int64 = STEP) -> Int64:
    """A Final module constant as a default -- one shared constant, not a
    per-call rebuild."""
    return n + by


@export
def scale(x: float, factor: float = 0.5) -> float:
    return x * factor


@export
def tag(data: bytes = b"ab") -> Int32:
    return len(data)


@export
def offset(at: tuple[Int32, Int32] = ORIGIN) -> Int32:
    return at[0] + at[1]


@export
def paint(shade: Color = Color.BLUE) -> Int32:
    return 100 if shade == Color.BLUE else 200


@export
def combine(a: Int64, *, b: Int64, c: Int64 = 5) -> Int64:
    """`b` is keyword-only and REQUIRED -- `$` is only legal after `|`, so the
    wrapper parses it as optional and reports the omission itself."""
    return a * 100 + b * 10 + c


@export
def blend(*, red: Int64, green: Int64, blue: Int64) -> Int64:
    """Three required keyword-only params: omitting all three exercises the
    report's 3+-name join, which uses an Oxford comma like CPython's."""
    return red * 10000 + green * 100 + blue


@export
def initial(text: str, idx: Int32 = 0, /) -> str:
    """Both params are positional-only: their kwlist entries are empty, so
    CPython refuses to match them by name."""
    return text[idx]


@export
class Counter:
    n: Int64
    step: Int64

    def __init__(self, n: Int64 = 0, *, step: Int64 = 1) -> None:
        self.n = n
        self.step = step

    def bump(self, by: Int64 = 1, *, twice: bool = False) -> Int64:
        self.n = self.n + by * self.step
        if twice:
            self.n = self.n + by * self.step
        return self.n


@export
class Base:
    lo: Int64
    hi: Int64
    scale: Int64

    # `scale`'s default trails the required `hi` on purpose: a default placed
    # BEFORE a required keyword-only param does not currently build (a
    # plain-TPy limitation tracked in BUGS.md, unrelated to the boundary).
    def __init__(self, lo: Int64, *, hi: Int64, scale: Int64 = 2) -> None:
        self.lo = lo
        self.hi = hi
        self.scale = scale

    def span(self) -> Int64:
        return (self.hi - self.lo) * self.scale


@export
class Derived(Base):
    """Inherits Base's __init__ rather than declaring its own, so the glue
    binds tp_init against the ANCESTOR's params -- the default and the
    keyword-only marker have to survive that hop, or the wrapper would demand
    both args positionally."""


@export
class Pair:
    """The REQUIRED keyword-only path on the class sites: `__init__` returns
    -1 rather than the wrapper's null sentinel when the check trips, and the
    method wrapper is a third emit site again."""

    a: Int64
    b: Int64

    def __init__(self, *, a: Int64, b: Int64) -> None:
        self.a = a
        self.b = b

    def weigh(self, *, factor: Int64) -> Int64:
        return (self.a + self.b) * factor

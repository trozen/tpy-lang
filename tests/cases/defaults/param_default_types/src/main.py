# The inverse of the default type check: every constant spelling that is
# legitimately assignable to its slot must still compile and bind its value.
# Widening (int literal -> Int64/float), the single-char str literal a Char
# slot accepts, and `None` into an Optional are the shapes most at risk of
# being caught by an over-eager check.
from typing import Final

from enum import IntEnum

from tpy import Char, Int8, Int32, Int64, StrView

STEP: Final[Int64] = 7


class Color(IntEnum):
    RED = 0
    BLUE = 1


def widen(n: Int64 = 5) -> Int64:
    return n


def as_float(x: float = 1) -> float:
    # Returned with a fractional part added: TPy widens the int literal to a
    # double at the boundary and CPython leaves it an int, so printing `x`
    # raw would render `1.0` vs `1` -- a representation difference, not a
    # value one.
    return x + 0.5


def negative(n: Int8 = -5) -> Int8:
    return n


def bracket(ch: Char = "[") -> str:
    return str(ch)


def raw(b: bytes = b"ab") -> Int32:
    return len(b)


def view(s: StrView = "hi") -> Int32:
    return len(s)


def flagged(on: bool = True) -> bool:
    return on


def optional(n: Int32 | None = None) -> Int32:
    return n if n is not None else -1


def from_final(n: Int64 = STEP) -> Int64:
    return n


def shade(c: Color = Color.BLUE) -> Int32:
    return 100 if c == Color.BLUE else 200


def wrapped(n: Int32 = Int32(7)) -> Int32:
    return n


class Box:
    n: Int32 = 3
    label: str = "b"

    def __init__(self) -> None:
        pass

    def scale(self, by: Int32 = 2) -> Int32:
        return self.n * by

    def stepped(self, by: Int64 = STEP) -> Int64:
        # A Final default on a METHOD: `self` occupies a parameter slot but no
        # default slot, so a misaligned check would compare STEP against the
        # receiver's type and reject this.
        return Int64(self.n) + by


def main() -> None:
    print(widen(), as_float(), negative(), bracket())
    print(raw(), view(), flagged(), optional())
    print(from_final(), shade(), wrapped())

    b = Box()
    print(b.n, b.label, b.scale(), b.stepped())


main()

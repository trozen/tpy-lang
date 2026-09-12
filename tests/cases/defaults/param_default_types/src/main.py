# The inverse of the default type check: every constant spelling that is
# legitimately assignable to its slot must still compile and bind its value.
# Widening (int literal -> int64/float), the single-char str literal a char
# slot accepts, and `None` into an Optional are the shapes most at risk of
# being caught by an over-eager check.
from typing import Final

from enum import IntEnum

from tpy import char, int8, int32, int64, StrView

STEP: Final[int64] = 7


class Color(IntEnum):
    RED = 0
    BLUE = 1


def widen(n: int64 = 5) -> int64:
    return n


def as_float(x: float = 1) -> float:
    # Returned with a fractional part added: TPy widens the int literal to a
    # double at the boundary and CPython leaves it an int, so printing `x`
    # raw would render `1.0` vs `1` -- a representation difference, not a
    # value one.
    return x + 0.5


def negative(n: int8 = -5) -> int8:
    return n


def bracket(ch: char = "[") -> str:
    return str(ch)


def raw(b: bytes = b"ab") -> int32:
    return len(b)


def view(s: StrView = "hi") -> int32:
    return len(s)


def flagged(on: bool = True) -> bool:
    return on


def optional(n: int32 | None = None) -> int32:
    return n if n is not None else -1


def from_final(n: int64 = STEP) -> int64:
    return n


def shade(c: Color = Color.BLUE) -> int32:
    return 100 if c == Color.BLUE else 200


def wrapped(n: int32 = int32(7)) -> int32:
    return n


class Box:
    n: int32 = 3
    label: str = "b"

    def __init__(self) -> None:
        pass

    def scale(self, by: int32 = 2) -> int32:
        return self.n * by

    def stepped(self, by: int64 = STEP) -> int64:
        # A Final default on a METHOD: `self` occupies a parameter slot but no
        # default slot, so a misaligned check would compare STEP against the
        # receiver's type and reject this.
        return int64(self.n) + by


def main() -> None:
    print(widen(), as_float(), negative(), bracket())
    print(raw(), view(), flagged(), optional())
    print(from_final(), shade(), wrapped())

    b = Box()
    print(b.n, b.label, b.scale(), b.stepped())


main()

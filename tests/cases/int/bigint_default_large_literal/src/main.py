# A >int32 int literal used as a DEFAULT value (function param, dataclass field
# member-init, and the generated ctor param) must render the ambiguity-safe
# BigInt ctor, not a bare C++ `long` (which converts to BigInt ambiguously on
# macOS). Small defaults stay small; a fixed-width int64 default must not wrap.
from dataclasses import dataclass

from tpy import int64


def g(x: int = 86400000000, y: int = -86400000000, z: int = 5) -> int:
    return x + y + z


# A fixed-int ctor wrapper around a >int32 literal must also pin its width at
# BOTH default sites -- the free-func param and the field member-init (which
# used to fall through to the parser's bare string for this spelling).
def w(x: int = int64(90000000000)) -> int:
    return x


@dataclass
class R:
    big: int = 90000000000
    neg: int = -90000000000
    small: int = 3
    wide: int64 = 90000000000
    wrapped: int = int64(90000000000)


def main() -> None:
    print(g())
    print(g(1, 2, 3))

    print(w())

    r = R()
    print(r.big, r.neg, r.small, r.wide, r.wrapped)


main()

# Default values declared on protocol methods propagate to call sites
# through structural-dispatch parameters. Pre-fix: defaults were dropped
# at parser time, so `f(p)` calling `p.method(x)` with one less arg than
# the protocol signature errored "expects N argument(s), got N-1".
from typing import Protocol
from tpy import Int32


class Counter(Protocol):
    # Two trailing defaults exercise both the simple case (whence=0)
    # and a chain (start defaults to 0 too, both fillable independently).
    def step(self, n: Int32 = 1, start: Int32 = 0) -> Int32: ...


class Tally:
    _v: Int32

    def __init__(self) -> None:
        self._v = 0

    def step(self, n: Int32 = 1, start: Int32 = 0) -> Int32:
        self._v = self._v + start + n
        return self._v


def bump(c: Counter) -> Int32:
    # Drop both defaults.
    a = c.step()
    # Drop just the trailing default.
    b = c.step(Int32(5))
    # Pass both explicitly (must keep working).
    cc = c.step(Int32(2), Int32(10))
    return a + b + cc


def main() -> None:
    t = Tally()
    print(bump(t))


main()

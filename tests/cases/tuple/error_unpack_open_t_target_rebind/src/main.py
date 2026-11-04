# Error: a for-head unpack target of an open `T` is a loop variable like the
# scalar one: a write through it passes, and rebinding it is the scalar's error.
from typing import Protocol
from tpy import int32


class Bumps(Protocol):
    def bump(self) -> None: ...


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def bump(self) -> None:
        self.n += 1


def rebind[T: Bumps](ps: list[tuple[T, int32]], y: T) -> int32:
    s = 0
    for x, n in ps:
        x.bump()
        # the rebind of the head target; the concrete twin re-points
        # (BUGS.md#open-t-head-target-rebind-twin-gap)
        x = y  # tpyc: error(/Cannot reassign loop variable 'x' of type 'T'/)
        s += n
    return s


def main() -> None:
    print(rebind([(P(1), 1)], P(2)))


main()

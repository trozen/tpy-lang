# A lambda capturing `self` has a non-Send frame: the capture is the raw
# `this` pointer (an alias into the origin thread's object), never a copy,
# so it must not pass a Send[Callable] boundary.
from typing import Callable
from tpy import int32, Send


def take(f: Send[Callable[[], int32]]) -> int32:
    return f()


class C:
    n: int32

    def __init__(self) -> None:
        self.n = 3

    def leak(self) -> int32:
        return take(lambda: self.n)  # tpyc: error(/'Callable\[\[\], int32\]' is not Send/)


def main() -> None:
    c = C()
    print(c.leak())


main()

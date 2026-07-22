# A lambda capturing `self` has a non-Send frame: the capture is the raw
# `this` pointer (an alias into the origin thread's object), never a copy,
# so it must not pass a Send[Callable] boundary.
from typing import Callable
from tpy import Int32, Send


def take(f: Send[Callable[[], Int32]]) -> Int32:
    return f()


class C:
    n: Int32

    def __init__(self) -> None:
        self.n = 3

    def leak(self) -> Int32:
        return take(lambda: self.n)  # tpyc: error(/'Callable\[\[\], Int32\]' is not Send/)


def main() -> None:
    c = C()
    print(c.leak())


main()

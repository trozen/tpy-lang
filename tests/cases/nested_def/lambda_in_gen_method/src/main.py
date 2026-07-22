# A lambda capturing self inside a simple-generator METHOD: the wrapper
# lambda holds the receiver as `this`, and the inner capture must spell
# `this` too (self renders `(*this)` in that context).
from typing import Callable, Iterator
from tpy import Int32


def apply(f: Callable[[Int32], Int32], v: Int32) -> Int32:
    return f(v)


class C:
    n: Int32

    def __init__(self) -> None:
        self.n = 10

    def emit(self, k: Int32) -> Iterator[Int32]:
        for i in range(k):
            yield apply(lambda x: x + self.n, i)


def main() -> None:
    c = C()
    for v in c.emit(3):
        print(v)


main()

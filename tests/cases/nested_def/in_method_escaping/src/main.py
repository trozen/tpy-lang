# An escaping nested def capturing self, stored into a Callable and called
# while the receiver is alive: the capture is `this` (an alias), so field
# updates made after the def are visible at call time -- CPython's late
# binding. The lambda adds a mixed by-value capture list (self + a local).
from typing import Callable
from tpy import Int32


def apply(f: Callable[[Int32], Int32], v: Int32) -> Int32:
    return f(v)


class Src:
    n: Int32

    def __init__(self) -> None:
        self.n = 5

    def reader(self) -> Callable[[Int32], Int32]:
        def get(x: Int32) -> Int32:
            return x + self.n

        return get

    def offset(self, k: Int32) -> Int32:
        return apply(lambda x: x + k + self.n, 1)


def main() -> None:
    s = Src()
    f = s.reader()
    s.n = 100
    print(f(1))
    print(s.offset(10))


main()

# Generator methods on the resumable frame: a method with a parameter and a
# try/finally (exercises method-shape finally helpers + param capture through
# the async _factory_args_forwarded seam), plus a readonly, no-param method
# (exercises the const factory + __self capture).
from typing import Iterator
from tpy import Int32


class Source:
    base: Int32

    def __init__(self, base: Int32) -> None:
        self.base = base

    def windowed(self, xs: list[Int32]) -> Iterator[Int32]:  # tpyc: ok
        i = 0
        try:
            while i < len(xs):
                yield xs[i] + self.base
                yield xs[i] * self.base
                i += 1
        finally:
            print("windowed done")

    def doubled(self) -> Iterator[Int32]:  # tpyc: ok
        yield self.base
        yield self.base * 2


def main() -> None:
    s = Source(10)
    data: list[Int32] = [1, 2]
    for v in s.windowed(data):
        print(v)
    for v in s.doubled():
        print(v)


main()

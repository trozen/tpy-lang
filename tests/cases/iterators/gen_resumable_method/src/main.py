# Generator methods on the resumable frame: a method with a parameter and a
# try/finally (exercises method-shape finally helpers + param capture through
# the async _factory_args_forwarded seam), plus a readonly, no-param method
# (exercises the const factory + __self capture).
from typing import Iterator
from tpy import int32


class Source:
    base: int32

    def __init__(self, base: int32) -> None:
        self.base = base

    def windowed(self, xs: list[int32]) -> Iterator[int32]:  # tpyc: ok
        i = 0
        try:
            while i < len(xs):
                yield xs[i] + self.base
                yield xs[i] * self.base
                i += 1
        finally:
            print("windowed done")

    def doubled(self) -> Iterator[int32]:  # tpyc: ok
        yield self.base
        yield self.base * 2


def main() -> None:
    s = Source(10)
    data: list[int32] = [1, 2]
    for v in s.windowed(data):
        print(v)
    for v in s.doubled():
        print(v)


main()

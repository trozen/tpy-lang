# A `while isinstance(...)` head in a simple generator would need the loop-entry
# extraction woven into the frame skeleton.
from typing import Iterator
from tpy import Int32


class Alpha:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v


class Beta:
    w: Int32

    def __init__(self, w: Int32) -> None:
        self.w = w


def ticks(x: Alpha | Beta) -> Iterator[Int32]:  # tpyc: error(/sgen.narrow_cond/)
    while isinstance(x, Alpha):
        yield x.v


def main() -> None:
    b = Beta(1)
    for v in ticks(b):
        print(v)


main()

# A `while isinstance(...)` head in a simple generator would need the loop-entry
# extraction woven into the frame skeleton.
from typing import Iterator
from tpy import int32


class Alpha:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Beta:
    w: int32

    def __init__(self, w: int32) -> None:
        self.w = w


def ticks(x: Alpha | Beta) -> Iterator[int32]:  # tpyc: error(/sgen.narrow_cond/)
    while isinstance(x, Alpha):
        yield x.v


def main() -> None:
    b = Beta(1)
    for v in ticks(b):
        print(v)


main()

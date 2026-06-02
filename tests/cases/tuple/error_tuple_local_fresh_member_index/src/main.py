# A safe (param-rooted) borrow element precedes the fresh one: the diagnostic
# must cite element 1 (the actually-fresh Box), not element 0.
from typing import Iterator
from tpy import Int32


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


def gen(b: Box) -> Iterator[tuple[Box, Box]]:
    t = (b, Box(Int32(7)))
    yield t  # tpyc: error(/element 1 \('Box'\) owns a freshly constructed/)


def main() -> None:
    shared = Box(Int32(5))
    for pair in gen(shared):
        print(pair[0].val, pair[1].val)


main()

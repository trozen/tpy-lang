# A generator unpacking its rebound owning tuple at the tuple's last use is
# refused (BUGS.md#frame-ptr-tuple-consuming-unpack-rejects).
from typing import Iterator

from tpy import Own, int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def make(n: int32, b: Box) -> tuple[Own[Box], Box]:
    return (Box(n), b)


def gen(v: Box) -> Iterator[int32]:
    p = make(1, v)
    p = make(2, v)
    # the last use of `p` would move its owned element out
    x, y = p  # tpyc: error(/res\.unpack/)
    x.n += 100
    yield x.n
    yield y.n


for x in gen(Box(50)):
    print(x)

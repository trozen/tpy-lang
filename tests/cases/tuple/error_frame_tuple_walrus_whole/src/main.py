# A walrus whose value is a generator's rebound tuple is refused, as the plain
# body's is: no walrus write spells a tuple of element pointers (BUGS.md#ptr-tuple-capture-and-binder-rejects).
from typing import Iterator

from tpy import Own, int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def make_mixed(n: int32, b: Box) -> tuple[Own[Box], Box]:
    return (Box(n), b)


# the reject is located at the frame's def, not at the walrus inside it
def gen(v: Box, c: bool) -> Iterator[int32]:  # tpyc: error(/expr\.walrus/)
    p = make_mixed(1, v)
    if (q := p)[0].n > 0:
        yield 0
    p = make_mixed(9, v)
    q[0].n += 100
    yield q[0].n
    yield p[0].n


def main() -> None:
    for x in gen(Box(50), True):
        print("walrus", x)


main()

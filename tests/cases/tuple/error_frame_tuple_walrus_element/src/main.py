# A walrus bound to an element of a generator's rebound tuple is refused, as
# the plain function body's is: the element is a pointer, and no walrus
# write spells an alias of it yet
# (BUGS.md#ptr-tuple-capture-and-binder-rejects).
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
    if (a := p[0]).n > 0:
        yield 0
    if c:
        p = make_mixed(9, v)
    a.n += 100
    yield a.n
    yield p[0].n


def main() -> None:
    for x in gen(Box(50), True):
        print("walrus", x)


main()

# Root rule A in a generator: a binding of a pointer tuple that owns nothing
# points only into a parameter's or self's storage, so re-pointing it at a
# local tuple of the generator is refused, located
# (BUGS.md#ptr-tuple-repoint-root-rejects).
from typing import Iterator

from tpy import Own, int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def make_mixed(n: int32, b: Box) -> tuple[Own[Box], Box]:
    return (Box(n), b)


def gen(v: Box) -> Iterator[int32]:
    q = (Box(7), v)
    p = make_mixed(1, v)
    yield p[0].n
    p = q  # tpyc: error(/here it points into 'q', a local of 'gen'/)
    p[0].n += 100
    yield q[0].n


def main() -> None:
    for x in gen(Box(50)):
        print(x)


main()

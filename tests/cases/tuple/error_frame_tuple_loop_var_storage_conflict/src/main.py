# A generator's tuple local that a `for` also binds is no pointer tuple, so
# its frame field has one form: a binding holding a fresh object and one
# borrowing an existing object cannot share it, and the second is refused
# although CPython runs it (BUGS.md#frame-tuple-mixed-binding-storage-rejects).
from typing import Iterator

from tpy import int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def gen(b: Box, pairs: list[tuple[Box, int32]]) -> Iterator[int32]:
    t = (Box(1), 2)
    yield t[0].n
    t = (b, 3)  # tpyc: error(/'t' is bound both to a tuple holding a fresh object and to one borrowing an existing object/)
    yield t[0].n
    for t in pairs:
        yield t[1]


def main() -> None:
    for x in gen(Box(5), [(Box(7), 8)]):
        print("conflict", x)


main()

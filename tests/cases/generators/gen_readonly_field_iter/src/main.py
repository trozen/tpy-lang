# Iteration over self fields inside generator methods with a readonly (const)
# receiver: direct narrowed-Optional field, borrow-alias local of it, and a
# plain-field alias through the simple-generator lambda -- the frames must
# spell const iterator/alias slots and const borrow locals. Bumper checks the
# non-readonly side: loop-var mutation reaches the field's elements (aliasing,
# no copy).

from typing import Iterator
from tpy import Int32


class Holder:
    lst: list[Int32] | None
    plain: list[Int32]

    def __init__(self):
        self.lst = [1, 2, 3]
        self.plain = [10, 20]

    def direct(self) -> Iterator[Int32]:
        if self.lst is not None:
            for x in self.lst:
                yield x

    def via_alias(self) -> Iterator[Int32]:
        h = self.lst
        if h is not None:
            for x in h:
                yield x

    def simple_alias(self) -> Iterator[Int32]:
        # Guards the const borrow-local spelling only: the simple-gen lambda
        # captures `a` by value (the documented escaping-closure snapshot), so
        # field mutations after creation are NOT observed here (BUGS.md).
        a = self.plain
        for x in a:
            yield x


class Counter:
    v: Int32

    def __init__(self, v: Int32):
        self.v = v


class Bumper:
    cells: list[Counter]

    def __init__(self):
        self.cells = [Counter(5), Counter(6)]

    def bump(self) -> Iterator[Int32]:
        for c in self.cells:
            c.v += 1
            yield c.v


def main() -> None:
    h = Holder()
    print(sum(h.direct()))
    print(sum(h.via_alias()))
    print(sum(h.simple_alias()))
    b = Bumper()
    print(sum(b.bump()))
    print(b.cells[0].v, b.cells[1].v)


main()

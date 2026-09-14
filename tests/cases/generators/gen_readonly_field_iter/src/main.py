# Iteration over self fields inside generator methods with a readonly (const)
# receiver: direct narrowed-Optional field, borrow-alias local of it, and a
# plain-field alias, through the resumable frame, where the alias is LIVE.
# The frames must spell const iterator/alias slots and const borrow locals.
# Bumper checks the non-readonly side: loop-var mutation reaches the field's
# elements (aliasing, no copy).

from typing import Iterator
from tpy import int32


class Holder:
    lst: list[int32] | None
    plain: list[int32]

    def __init__(self):
        self.lst = [1, 2, 3]
        self.plain = [10, 20]

    def direct(self) -> Iterator[int32]:
        if self.lst is not None:
            for x in self.lst:
                yield x

    def via_alias(self) -> Iterator[int32]:
        h = self.lst
        if h is not None:
            for x in h:
                yield x

    def simple_alias(self) -> Iterator[int32]:
        # single yield: the same frame alias as `live_alias` below
        a = self.plain
        for x in a:
            yield x

    # generator METHOD, TWO yields (frame): the alias binds the field's
    # ADDRESS (`a = &(__self.plain);`), so a write to the field after the
    # bind is observed through it -- CPython's name binding.
    def live_alias(self) -> Iterator[int32]:
        a = self.plain  # tpyc: ok
        yield a[0]
        self.plain[0] = 111
        yield a[0]


# free generator, NON-self receiver: the same container-field alias off a
# PARAM, which had no admitted source row before.
def alias_param(h: Holder) -> Iterator[int32]:
    a = h.plain  # tpyc: ok
    yield a[1]
    h.plain[1] = 222
    yield a[1]


class Counter:
    v: int32

    def __init__(self, v: int32):
        self.v = v


class Bumper:
    cells: list[Counter]

    def __init__(self):
        self.cells = [Counter(5), Counter(6)]

    def bump(self) -> Iterator[int32]:
        for c in self.cells:
            c.v += 1
            yield c.v


def main() -> None:
    h = Holder()
    print(sum(h.direct()))
    print(sum(h.via_alias()))
    print(sum(h.simple_alias()))
    h2 = Holder()
    print("live", list(h2.live_alias()))
    h3 = Holder()
    print("param", list(alias_param(h3)))
    b = Bumper()
    print(sum(b.bump()))
    print(b.cells[0].v, b.cells[1].v)


main()

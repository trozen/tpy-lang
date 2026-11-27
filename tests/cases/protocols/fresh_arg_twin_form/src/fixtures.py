# Protocol-parameter callees and their concrete twins (same body, concrete
# parameter type), shared by every section of the case.
from typing import Iterable, Iterator, Protocol
from tpy import int32


class Bumper(Protocol):
    def bump(self) -> None: ...

    def get(self) -> int32: ...


class Acc:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def bump(self) -> None:
        self.n += 1

    def get(self) -> int32:
        return self.n


class Bag:
    items: list[int32]

    def __init__(self, n: int32) -> None:
        self.items = [i for i in range(n)]

    def __iter__(self) -> Iterator[int32]:
        return iter(self.items)


def total(it: Iterable[int32]) -> int32:
    s = 0
    for x in it:
        s += x
    return s


def total_l(it: list[int32]) -> int32:
    s = 0
    for x in it:
        s += x
    return s


def total_b(it: Bag) -> int32:
    s = 0
    for x in it:
        s += x
    return s


# The callee writes the fresh object through the protocol and reads the
# write back: the write lands in the argument itself, not in a copy.
def bump_twice(b: Bumper) -> int32:
    b.bump()
    b.bump()
    return b.get()


def bump_twice_c(b: Acc) -> int32:
    b.bump()
    b.bump()
    return b.get()


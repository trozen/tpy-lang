# A for-loop over an existing local rebinds it: post-loop reads see the
# last element (CPython), not a stale pre-loop value.
from typing import Iterator
from tpy import int32


def scalar() -> None:
    x = 100
    for x in range(3):
        pass
    print(x)


def param_rebind(x: int32) -> None:
    for x in range(2):
        pass
    print(x)


def fresh_stays_scoped() -> None:
    total = 0
    for y in range(3):
        total = total + y
    print(total)


# A name two sibling loop bodies bind is ONE local declared ahead of the first
# loop, so a later loop head over that name binds that local too.
def body_local_then_head() -> None:
    total = 0
    for a in range(1, 3):
        n = a * 10
        total += n
    for b in range(1, 3):
        n = b * 100
        total += n
    for n in range(2, 5):  # tpyc: ok
        total += n
    print("body_local_then_head", total)


# The same with heads that may run zero times: the local is maybe-assigned,
# and the head still binds its one declaration.
def body_local_unprovable(k: int32, end: int) -> None:
    total = end
    for a in range(1, k):
        n = end + a
        total += n
    for b in range(1, k):
        n, m = end * b, b
        total += n + m
    for n in range(2, end):  # tpyc: ok
        total += n
    print("body_local_unprovable", total)


def body_local_over_list(xs: list[int32]) -> None:
    total = 0
    for a in range(1, 3):
        n = a
        total += n
    for b in range(1, 3):
        n = b
        total += n
    for n in xs:  # tpyc: ok
        total += n
    n = 1000
    print("body_local_over_list", total + n)


class Acc:
    total: int32

    def __init__(self) -> None:
        self.total = 0

    # method position
    def run(self, k: int32) -> None:
        for a in range(1, k):
            n = a
            self.total += n
        for b in range(1, k):
            n = b
            self.total += n
        for n in range(2, 5):  # tpyc: ok
            self.total += n
        print("method", self.total)


# generator position, no suspension inside the loops
def gen(k: int32) -> Iterator[int32]:
    total = 0
    for a in range(1, k):
        n = a
        total += n
    for b in range(1, k):
        n = b
        total += n
    for n in range(2, 5):  # tpyc: ok
        total += n
    yield total


def main() -> None:
    scalar()
    param_rebind(50)
    fresh_stays_scoped()
    body_local_then_head()
    body_local_unprovable(3, 6)
    body_local_over_list([7, 8, 9])
    Acc().run(3)
    for v in gen(3):
        print("gen", v)


main()

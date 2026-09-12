# A reassigned tuple frame local whose two inits disagree on one element: one
# binds a fresh object (the frame must own it) and the other borrows an
# existing one (the frame must point at it). There is no single field form, so
# the decl is declined rather than picking one and copying or dangling.
from typing import Iterator

from tpy import int32


class A:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def g(a: A, c: bool) -> Iterator[int32]:
    t = (a, 2)
    if c:
        t = (A(9), 3)  # tpyc: error(/need different storage/)
    yield t[0].v
    yield t[1]


def main() -> None:
    a = A(1)
    for n in g(a, True):
        print(n)


main()

# A tuple-literal frame local whose element is a TERNARY over a fresh object
# and an existing one. Sema stamps it a VALUE capture, but the lvalue arm would
# then be COPIED where CPython aliases, so the frame local declines it rather
# than owning the element (BUGS.md#reference-ternary-position-gaps).
from typing import Iterator

from tpy import int32


class A:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def g(a: A, c: bool) -> Iterator[int32]:
    t = (a if c else A(9), 2)  # tpyc: error(/expr\.tuple_literal/)
    yield t[0].v
    a.v = 42
    yield t[0].v


def main() -> None:
    a = A(1)
    for n in g(a, True):
        print(n)


main()

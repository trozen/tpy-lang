# An alias of an element held by value in an owning tuple frame slot that the
# body later rebinds is rejected: the rebind would write the new tuple into
# the slot the alias points into, so it would see the new element where
# CPython keeps the old object (BUGS.md#resumable-alias-identity).
from typing import Iterator

from tpy import int32


class A:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def lit(c: bool) -> Iterator[int32]:
    t = (A(1), A(2))
    saved = t[1]  # tpyc: error(/binding 'saved' to an element inside 't' is not yet supported in a generator.*last reassignment of 't'$/)
    yield saved.v
    if c:
        t = (A(9), A(8))
    yield saved.v


def main() -> None:
    for v in lit(True):
        print(v)


main()

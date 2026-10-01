# An alias of an element of a list held by value in a tuple frame slot the
# body rebinds is rejected like a direct element alias
# (BUGS.md#resumable-alias-identity). Binding a copy is no workaround
# here yet: `copy(t[0][1])` does not compile
# (BUGS.md#tuple-elem-copy-mixed-or-list-rejects).
from typing import Iterator

from tpy import int32


class A:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def g(c: bool) -> Iterator[int32]:
    t = ([A(1), A(3)], 2)
    saved = t[0][1]  # tpyc: error(/binding 'saved' to an element inside 't' is not yet supported in a generator.*last reassignment of 't'$/)
    yield saved.v
    if c:
        t = ([A(9), A(7)], 8)
    yield saved.v


def main() -> None:
    for v in g(True):
        print(v)


main()

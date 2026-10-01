# An alias of an element held by value in a tuple that is itself an element
# of a list frame slot rebound after the alias is rejected, like the tuple
# slot's own element (BUGS.md#resumable-alias-identity): the path from the
# slot to the element crosses a tuple held by value. Binding a copy is no
# workaround here yet: `copy(xs[0][1])` does not compile
# (BUGS.md#tuple-elem-copy-mixed-or-list-rejects).
from typing import Iterator

from tpy import int32


class A:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def g(c: bool) -> Iterator[int32]:
    xs = [(A(1), A(2))]
    saved = xs[0][1]  # tpyc: error(/binding 'saved' to an element inside 'xs' is not yet supported in a generator.*last reassignment of 'xs'$/)
    yield saved.v
    if c:
        xs = [(A(9), A(8))]
    yield saved.v


def main() -> None:
    for v in g(True):
        print(v)


main()

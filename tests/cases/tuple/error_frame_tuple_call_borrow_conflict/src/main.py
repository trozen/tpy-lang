# A borrowing literal and an owning call bound to one frame tuple local across a
# yield are rejected: the two inits want different field forms for the element.
from typing import Iterator

from tpy import Int32, Own


class A:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v


def make_pair() -> Own[tuple[A, Int32]]:
    return (A(7), 3)


def g(a: A, c: bool) -> Iterator[Int32]:
    if c:
        t = (a, 2)
    else:
        t = make_pair()  # tpyc: error(/need different storage/)
    yield t[0].v
    # `a` outlives the tuple, so element 0 of the first init really is a borrow
    a.v = 99
    yield t[0].v


def main() -> None:
    a = A(1)
    for n in g(a, True):
        print(n)


main()

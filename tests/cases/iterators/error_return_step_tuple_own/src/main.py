# Error: a step at an `Own` tuple element escapes as at the scalar `-> Own[P]`
# twin: the frame does not own it to hand over.
from typing import Iterator
from tpy import Own, int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def source(k: int32) -> Iterator[P]:
    p = P(k)
    yield p


def escape(k: int32) -> tuple[Own[P], int32]:
    for p in source(k):
        # the step at the owning element
        return (p, 1)  # tpyc: error(/Cannot return 'p': it borrows an element of a generator/)
    return (P(0), 0)


def main() -> None:
    print(escape(7)[0].n)


main()

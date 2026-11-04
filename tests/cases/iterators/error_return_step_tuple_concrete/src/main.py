# Error: a tuple literal holding a generator's step, returned past its
# iteration, dangles at a concrete element type too.
from typing import Iterator
from tpy import int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def source(k: int32) -> Iterator[P]:
    p = P(k)
    yield p


def escape() -> tuple[P, int32]:
    for p in source(7):
        # the step escapes inside the returned tuple
        return (p, 1)  # tpyc: error(/Cannot return 'p': it borrows an element of a generator/)
    return (P(0), 0)


def main() -> None:
    print(escape()[0].n)


main()

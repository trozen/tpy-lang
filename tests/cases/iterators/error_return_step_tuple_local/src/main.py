# Error: a tuple local holding a generator's step is an alias of the step,
# so returning it dangles past the iteration as the literal does.
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
        t = (p, 1)
        # the return of the tuple alias
        return t  # tpyc: error(/Cannot return 't': it borrows an element of a generator/)
    return (P(0), 0)


def main() -> None:
    print(escape()[0].n)


main()

# Error: a callee defined below lends into a dying temporary list
# (docs/LANGUAGE_FEATURES.md, Reference preservation through generic generators).
from typing import Iterator
from tpy import int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def gen_late[T](x: T) -> Iterator[T]:
    yield first_below([x])  # tpyc: error(/Cannot yield local or temporary as reference/)


def first_below[T](xs: list[T]) -> T:
    return xs[0]


def main() -> None:
    for q in gen_late(P(1)):
        print(q.n)


main()

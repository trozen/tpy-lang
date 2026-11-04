# Error: a fresh copy under a generic `Iterator[T]` dangles, as concretely
# (docs/LANGUAGE_FEATURES.md, Reference preservation through generic generators).
from typing import Iterator
from tpy import copy, int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def clones[T](xs: list[T]) -> Iterator[T]:
    for x in xs:
        yield copy(x)  # tpyc: error(/Cannot yield local or temporary as reference.*Iterator\[Own\[T\]\]/)


def main() -> None:
    for p in clones([P(1)]):
        print(p.n)


main()

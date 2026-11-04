# Error: a fresh copy under a concrete `Iterator[P]` dangles
# (docs/LANGUAGE_FEATURES.md, Reference preservation through generic generators).
from typing import Iterator
from tpy import copy, int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def clones(xs: list[P]) -> Iterator[P]:
    for x in xs:
        yield copy(x)  # tpyc: error(/Cannot yield local or temporary as reference.*Iterator\[Own\[P\]\]/)


def main() -> None:
    for p in clones([P(1)]):
        print(p.n)


main()

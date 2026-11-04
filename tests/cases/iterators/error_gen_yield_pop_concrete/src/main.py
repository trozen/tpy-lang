# Error: a popped value is fresh; a concrete `Iterator[P]` cannot lend it
# (docs/LANGUAGE_FEATURES.md, Reference preservation through generic generators).
from typing import Iterator
from tpy import int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def drain(xs: list[P]) -> Iterator[P]:
    while len(xs) > 0:
        yield xs.pop()  # tpyc: error(/Cannot yield local or temporary as reference.*Iterator\[Own\[P\]\]/)


def main() -> None:
    ps = [P(1)]
    for p in drain(ps):
        print(p.n)


main()

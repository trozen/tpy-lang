# Error: an element of a tuple step is refused as a temporary, though it
# lives one step (BUGS.md#tuple-step-element-yield-rejects).
from typing import Iterator
from tpy import int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def pairs_of(ps: list[P]) -> Iterator[tuple[P, int32]]:
    for p in ps:
        yield (p, 1)


def firsts(it: Iterator[tuple[P, int32]]) -> Iterator[P]:
    for t in it:
        # the relay of the tuple step's first element
        yield t[0]  # tpyc: error(/Cannot yield local or temporary as reference/)


def main() -> None:
    ps = [P(1), P(2)]
    for p in firsts(pairs_of(ps)):
        p.n += 10
    print(ps[0].n, ps[1].n)


main()

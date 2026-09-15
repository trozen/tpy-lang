# The rule is the same at a generator's borrow YIELD -- the frame survives
# suspension, so the yielded borrow must root in storage nothing reseats --
# and only the verb differs.
# XS is reference-typed, so `Cannot reassign global variable 'XS' of non-value
# type` is the sibling reject that fires instead when sema reaches `reset`
# first -- which is why this leg offers no `Iterator[Own[...]]` escape, only
# the parity-preserving remedy of dropping the rebind.
from typing import Iterator

from tpy import int32

XS = [1, 2, 3]


def rows() -> Iterator[list[int32]]:
    yield XS  # tpyc: error(/Cannot yield a borrow of module variable 'XS'.*Stop rebinding 'XS'/)


def reset() -> None:
    global XS
    XS = [9]


def main() -> None:
    for row in rows():
        print(len(row))
    reset()


main()

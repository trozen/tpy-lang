# The adjacent shape the container pointer-slot decl must keep rejecting: the
# init call BORROWS its result (a generic identity returning `T&`) instead of
# owning it, so there is no rvalue to seat in a `__slot_N`.
from typing import Sized

from tpy import int32


def identity[T: Sized](x: T) -> T:
    return x


def reassigned(other: list[int32]) -> None:
    zs = identity([1])  # tpyc: error(/not yet supported/)
    zs = other
    print(len(zs))


def main() -> None:
    reassigned([1, 2])


main()

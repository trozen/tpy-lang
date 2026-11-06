# A Callable returned from a generic whose return is a type parameter holds
# its result by value -- at `T = P` it is a Callable[[], Own[P]] -- so it
# does not fit a Callable whose bare return is a borrow.
# Rule: docs/LANGUAGE_FEATURES.md "Lambda / Closures", the Restriction "A
# Callable's borrowed result points into its arguments only".
from typing import Callable
from tpy import Own, int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def mk() -> Own[P]:
    return P(1)


def pick[T](f: Callable[[], T]) -> Callable[[], T]:
    return f


def main() -> None:
    c: Callable[[], Own[P]] = mk
    # The subject: pick(c) hands back a value; h promises a borrow.
    h: Callable[[], P] = pick(c)  # tpyc: error(/expected Callable\[\[\], P\], got Callable\[\[\], Own\[P\]\]: it returns Own\[P\], but Callable\[\[\], P\] returns a borrow/)
    print(h().n)


main()

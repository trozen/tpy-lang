# An Fn value bound to a Callable slot with a bare reference return is
# refused: an Fn may hand back a borrow of its own state (a __call__ object
# returning its field), which a Callable's borrow may not point into.
# Rule: docs/LANGUAGE_FEATURES.md "Lambda / Closures", the Restriction "A
# Callable's borrowed result points into its arguments only".
from typing import Callable
from tpy import Fn, int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Lender:
    p: P

    def __init__(self) -> None:
        self.p = P(1)

    def __call__(self) -> P:
        return self.p


def use(f: Fn[[], P]) -> None:
    # The subject: the Fn value at a lending Callable slot.
    g: Callable[[], P] = f  # tpyc: error(/an Fn may return a borrow of its own state, and a borrow returned through Callable\[\[\], P\] must point into its arguments; declare the parameter Callable\[\[\], P\], or return Own\[P\] from both/)
    g().n += 10
    print(g().n)


def main() -> None:
    s = Lender()
    use(s)
    print(s.p.n)


main()

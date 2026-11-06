# A callable object cannot stand at a Callable slot returning a borrow: it
# is state its __call__'s signature says nothing about, so that signature
# cannot promise the result points into its arguments (this one lends its
# own field). An Fn slot takes it.
# Rule: docs/LANGUAGE_FEATURES.md "Lambda / Closures", the Restriction "A
# Callable's borrowed result points into its arguments only".
from typing import Callable
from tpy import int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Lender:
    p: P

    def __init__(self) -> None:
        self.p = P(7)

    def __call__(self) -> P:
        return self.p


def main() -> None:
    obj = Lender()
    cb: Callable[[], P] = obj  # tpyc: error(/'Lender' cannot be passed as Callable\[\[\], P\] in variable 'cb': it is an object/)
    print(cb().n)


main()

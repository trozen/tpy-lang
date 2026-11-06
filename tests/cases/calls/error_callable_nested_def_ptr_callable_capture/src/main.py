# A nested def capturing a Callable whose result is a Ptr captures state: rule
# R roots only a bare reference-type or readonly result in the arguments, so a
# Ptr result may point into the callable's own target (here Lender.p), which
# the nested def's signature says nothing about. A captured Callable returning
# a bare P is no state (calls/callable_def_rule, sec_nested_locals).
# Rule: docs/LANGUAGE_FEATURES.md "Lambda / Closures", the Restriction "A
# Callable's borrowed result points into its arguments only".
from typing import Callable
from tpy import int32, Ptr


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Lender:
    p: P

    def __init__(self) -> None:
        self.p = P(11)

    def __call__(self, q: P) -> Ptr[P]:
        return self.p


def outer() -> Callable[[P], P]:
    lend: Callable[[P], Ptr[P]] = Lender()

    def get(q: P) -> P:
        return lend(q)
    # The subject: get captures lend, whose Ptr result may point into Lender.
    f: Callable[[P], P] = get  # tpyc: error(/'get' cannot be passed as Callable\[\[P\], P\]: it captures 'lend'/)
    return f


print(outer()(P(2)).n)

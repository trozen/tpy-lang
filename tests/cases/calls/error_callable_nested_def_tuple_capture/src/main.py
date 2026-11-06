# A nested def capturing a tuple that holds a class captures state, although
# the tuple is a value type: the copy it holds keeps a P, which its
# signature says nothing about, so it cannot stand at a Callable slot
# returning a borrow (an int32 or str capture can: calls/callable_def_rule,
# sec_nested_locals).
# Rule: docs/LANGUAGE_FEATURES.md "Lambda / Closures", the Restriction "A
# Callable's borrowed result points into its arguments only".
from typing import Callable
from tpy import int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def make(h: P) -> None:
    t = (h, 1)

    def get(q: P) -> P:
        return q if t[0].n > 0 else q
    # The subject: get captures t, a tuple holding a P.
    w: Callable[[P], P] = get  # tpyc: error(/'get' cannot be passed as Callable\[\[P\], P\]: it captures 't'/)
    print(w(P(1)).n)


make(P(41))

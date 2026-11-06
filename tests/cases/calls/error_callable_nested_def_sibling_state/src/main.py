# A nested def calling a sibling nested def that captures state (`get`
# reads the parameter h) holds that state too, which its own signature says
# nothing about, so it cannot stand at a Callable slot returning a borrow
# even when every body lends an argument; an Fn slot takes it, and a
# sibling capturing no state is no state (calls/callable_def_rule,
# sec_siblings).
# Rule: docs/LANGUAGE_FEATURES.md "Lambda / Closures", the Restriction "A
# Callable's borrowed result points into its arguments only".
from typing import Callable
from tpy import int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def run(h: P) -> None:
    def get(q: P) -> P:
        return q if h.n > 0 else q

    def middle(q: P) -> P:
        return get(q)
    # The subject: middle captures get, which captures h.
    w: Callable[[P], P] = middle  # tpyc: error(/'middle' cannot be passed as Callable\[\[P\], P\]: it captures 'get', which captures 'h'/)
    p = P(1)
    w(p).n += 1
    print(p.n)


run(P(5))

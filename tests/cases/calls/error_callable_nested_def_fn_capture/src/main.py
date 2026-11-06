# A nested def capturing an Fn value is stateful at a Callable slot returning
# a borrow: an Fn may be a __call__ object lending its own state, which the
# nested def's signature says nothing about. (A captured Callable is no
# state: its result points into its arguments only.)
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


def outer(g: Fn[[], P]) -> None:
    def get(q: P) -> P:
        return q if g().n > 0 else q
    # The subject: get captures the Fn value g.
    f: Callable[[P], P] = get  # tpyc: error(/'get' cannot be passed as Callable\[\[P\], P\]: it captures 'g'/)
    f(P(2)).n += 1


def main() -> None:
    outer(Lender())


main()

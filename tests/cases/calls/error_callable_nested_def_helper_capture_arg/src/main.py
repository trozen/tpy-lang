# The nested def twin of calls/error_callable_lambda_helper_capture_arg: a
# def passing a capture and its argument to a helper whose contract lends
# either is refused at a Callable slot -- by its capture alone, since only
# signatures decide and its own says nothing about the state it holds.
# Rule: docs/LANGUAGE_FEATURES.md "Lambda / Closures", the Restriction "A
# Callable's borrowed result points into its arguments only".
from typing import Callable
from tpy import int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def run() -> None:
    local = P(5)

    def outer(q: P) -> P:
        return helper(local, q)
    # The subject: helper may hand back `local`.
    k: Callable[[P], P] = outer  # tpyc: error(/'outer' cannot be passed as Callable\[\[P\], P\]: it captures 'local'/)
    p = P(1)
    k(p).n += 1
    print(p.n, local.n)


def helper(a: P, b: P) -> P:
    return b


run()

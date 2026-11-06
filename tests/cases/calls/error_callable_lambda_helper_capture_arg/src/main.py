# A lambda passing a capture and its argument to a helper whose contract
# lends either is refused at a Callable slot, as its nested def twin is
# (calls/error_callable_nested_def_helper_capture_arg): the helper's
# signature, not its body, says what the result points into.
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
    # The subject: helper may hand back `local`.
    k: Callable[[P], P] = lambda q: helper(local, q)  # tpyc: error(/this lambda's result points into 'local', which it captures/)
    p = P(1)
    k(p).n += 1
    print(p.n, local.n)


def helper(a: P, b: P) -> P:
    return b


run()

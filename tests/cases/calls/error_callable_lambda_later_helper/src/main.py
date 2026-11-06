# A lambda at a Callable slot lending a capture through a helper defined
# AFTER it is refused as when the helper comes first: each call's result is
# read off its callee's signature, never its body.
# Rule: docs/LANGUAGE_FEATURES.md "Lambda / Closures", the Restriction "A
# Callable's borrowed result points into its arguments only".
from typing import Callable
from tpy import int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def run() -> None:
    local = P(41)
    # The subject: helper is analyzed later; its contract lends its argument.
    k: Callable[[], P] = lambda: helper(local)  # tpyc: error(/this lambda's result points into 'local', which it captures/)
    print(k().n)


def helper(p: P) -> P:
    return p


run()

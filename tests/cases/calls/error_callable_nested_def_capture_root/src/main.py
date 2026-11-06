# A nested def capturing state cannot stand at a Callable slot returning a
# borrow: its signature cannot promise the result points into its
# arguments, whatever its body returns (here a capture, through a helper
# defined after it).
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

    def get() -> P:
        return helper(local)
    # The subject: get captures local.
    k: Callable[[], P] = get  # tpyc: error(/'get' cannot be passed as Callable\[\[\], P\]: it captures 'local'/)
    print(k().n)


def helper(p: P) -> P:
    return p


run()

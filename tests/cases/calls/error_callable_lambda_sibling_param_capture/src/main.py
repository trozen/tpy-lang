# A lambda at a Callable slot whose result is a call to a sibling nested def
# that captures state is refused by signatures alone: the sibling's own
# signature cannot promise its result points into its arguments, whatever
# its body returns and whatever order its helper is defined in.
# Rule: docs/LANGUAGE_FEATURES.md "Lambda / Closures", the Restriction "A
# Callable's borrowed result points into its arguments only".
from typing import Callable
from tpy import int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def make() -> Callable[[P], P]:
    h = P(41)

    def get(p: P) -> P:
        return helper(h)
    # The subject: the lambda's result is get's, and get captures h.
    k: Callable[[P], P] = lambda p: get(p)  # tpyc: error(/this lambda cannot be passed as Callable\[\[P\], P\]: its result is the result of 'get', which captures 'h'/)
    return k


def helper(p: P) -> P:
    return p


def main() -> None:
    k = make()
    print(k(P(1)).n)


main()

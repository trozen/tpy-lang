# A lambda at a Callable slot calling a method of a captured object lends that
# object, its receiver: the receiver is an operand of the call, so the result
# points into the capture, not into an argument.
# Rule: docs/LANGUAGE_FEATURES.md "Lambda / Closures", the Restriction "A
# Callable's borrowed result points into its arguments only".
from typing import Callable
from tpy import int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class O:
    p: P

    def __init__(self) -> None:
        self.p = P(5)

    def get(self, x: P) -> P:
        return self.p


def run() -> None:
    o = O()
    # The subject: o.get lends o.
    k: Callable[[P], P] = lambda x: o.get(x)  # tpyc: error(/this lambda's result points into 'o', which it captures/)
    k(P(1)).n += 1
    print(o.p.n)


run()

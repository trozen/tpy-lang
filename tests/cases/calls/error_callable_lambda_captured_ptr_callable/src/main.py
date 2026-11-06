# A lambda whose result is a call through a Callable value returning a Ptr:
# rule R roots only a bare reference-type or readonly result in the arguments,
# so a Ptr may point into the callable's own target (here Lender.p) and is
# refused, whatever the callee (captured, a parameter's field, a temporary)
# and arguments. A captured Callable[[P], P] roots in q
# (calls/callable_def_rule, sec_lambda).
from typing import Callable
from tpy import int32, Ptr


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Lender:
    p: P

    def __init__(self, n: int32) -> None:
        self.p = P(n)

    def __call__(self, q: P) -> Ptr[P]:
        return self.p


def mk(n: int32) -> Callable[[P], P]:
    lend: Callable[[P], Ptr[P]] = Lender(n)
    # The subject: the result points into lend's target, which dies with k.
    k: Callable[[P], P] = lambda q: lend(q)  # tpyc: error(/this lambda's result comes from a call through 'lend', whose signature cannot promise/)
    return k


def main() -> None:
    k = mk(11)
    r = k(P(2))
    k = mk(22)
    print(r.n, k(P(0)).n)


main()

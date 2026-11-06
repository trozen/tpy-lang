# A lambda whose result is a rule-R call through a Callable value with a
# temporary argument: the result roots in that temporary, which dies when
# the lambda returns -- refused as the module-function twin
# (`lambda q: ident(P(5))`) is.
from typing import Callable
from tpy import int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def ident(p: P) -> P:
    return p


def mk() -> Callable[[P], P]:
    cb: Callable[[P], P] = ident
    # The subject: cb's result points into P(5).
    k: Callable[[P], P] = lambda q: cb(P(5))  # tpyc: error(/Cannot return a borrow of a temporary from this lambda: its result points into 'P\(\.\.\.\)'/)
    return k


def main() -> None:
    print(mk()(P(1)).n)


main()

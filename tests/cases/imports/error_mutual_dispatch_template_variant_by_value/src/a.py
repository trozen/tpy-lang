from typing import Iterable
from tpy import dispatch, int32
import b


class Summer:
    @dispatch
    def total(self, x: int32) -> int32:
        return x

    # A variant with its own template head (the `Iterable` protocol
    # parameter) is defined in `a.hpp`, which sees `Val` only
    # forward-declared: the by-value peer parameter must be rejected.
    @dispatch
    def total(self, x: Iterable[int32], v: b.Val) -> int32:  # tpyc: error(/Cyclic import/)
        n = v.n
        for e in x:
            n += e
        return n


def run() -> int32:
    return Summer().total([1, 2], b.Val(7))

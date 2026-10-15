from tpy import int32
import b


class Holder[T]:
    n: int32

    # A generic class's constructor body is template code in `a.hpp`, which
    # sees `Val` only forward-declared: the by-value peer parameter must be
    # rejected.
    def __init__(self, tag: T, v: b.Val) -> None:  # tpyc: error(/Cyclic import/)
        self.tag = tag
        self.n = v.n


def run() -> int32:
    return Holder(1, b.Val(7)).n

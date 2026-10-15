from tpy import int32
import b


class Adder[T]:
    def __init__(self, tag: T) -> None:
        self.tag = tag

    # A generic class's member body, and its hidden friend `operator+`, are
    # template code in `a.hpp`, which sees `Val` only forward-declared: the
    # by-value peer parameter must be rejected.
    def __add__(self, x: b.Val) -> int32:  # tpyc: error(/Cyclic import/)
        return x.n


def run() -> int32:
    return Adder(1) + b.Val(7)

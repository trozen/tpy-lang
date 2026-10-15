from tpy import int32
import b


class Adder:
    # The C++ `operator+` that delegates to `__add__` is defined in the class,
    # in `a.hpp`, which sees `Val` only forward-declared: the by-value peer
    # parameter must be rejected although `__add__`'s body is in `a.cpp`.
    def __add__(self, x: b.Val) -> int32:  # tpyc: error(/Cyclic import/)
        return x.n


def run() -> int32:
    return Adder() + b.Val(7)

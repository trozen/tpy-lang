# A `Literal[...]` value no variant of a record's dunder set can take is
# refused, as the explicit call refuses it (a value a variant takes by a
# conversion is accepted: operators/dispatch_operator_conversions).
from typing import Literal
from tpy import dispatch, int32


class V:
    def __init__(self) -> None:
        pass

    @dispatch
    def __add__(self, x: str) -> int32:
        return 2

    @dispatch
    def __add__(self, x: bytes) -> int32:
        return 1


def f(k: Literal[3, 4]) -> None:
    v = V()
    # `k`'s base type is `int32`: neither variant takes it.
    print(v + k)  # tpyc: error(/Invalid operand types for '\+': V and Literal\[3, 4\]/)


f(3)

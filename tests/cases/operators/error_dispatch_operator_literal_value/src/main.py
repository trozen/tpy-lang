# A `Literal[...]` value a dunder set takes only by converting it to another
# type than its base is refused (BUGS.md#operator-dunder-value-conversion-refused).
from typing import Literal
from tpy import dispatch, int32


class V:
    def __init__(self) -> None:
        pass

    @dispatch
    def __add__(self, x: int) -> int32:
        return 2

    @dispatch
    def __add__(self, x: float) -> int32:
        return 1


def f(k: Literal[3, 4]) -> None:
    v = V()
    # `k`'s base type is `int32`: both variants convert it to another type.
    print(v + k)  # tpyc: error(/Invalid operand types for '\+': V and Literal\[3, 4\]/)


f(3)

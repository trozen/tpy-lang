# A bound broken only in an overload implementation's signature.
from typing import overload
from tpy import ValueType, int32


class Box[T: ValueType]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = v


@overload
def f(x: int32) -> int32: ...
@overload
def f(x: str) -> int32: ...
def f(x: int32 | str) -> Box[list[int32]] | int32:  # tpyc: error(/Type argument 'list\[int32\]' does not satisfy bound 'ValueType' for type parameter 'T' of 'Box'/)
    return 1

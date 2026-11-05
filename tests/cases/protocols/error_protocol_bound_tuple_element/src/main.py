# A bound broken inside a tuple element of a parameter.
from typing import Protocol
from tpy import ValueType, int32


class P[T: ValueType](Protocol):
    def size(self) -> int32: ...


def use(t: tuple[P[list[int32]], int32]) -> int32:  # tpyc: error(/Type argument 'list\[int32\]' does not satisfy bound 'ValueType' for type parameter 'T' of 'P'/)
    return t[1]

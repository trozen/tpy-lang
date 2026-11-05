# A bound broken inside another type parameter's bound.
from typing import Protocol
from tpy import ValueType, int32


class P[T: ValueType](Protocol):
    def size(self) -> int32: ...


class Q[U: P[list[int32]]](Protocol):  # tpyc: error(/Type argument 'list\[int32\]' does not satisfy bound 'ValueType' for type parameter 'T' of 'P'/)
    def g(self) -> int32: ...

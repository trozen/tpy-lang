# A bound broken in an overload implementation's own type-parameter bound.
from typing import Protocol, overload
from tpy import ValueType, int32


class P[T: ValueType](Protocol):
    def size(self) -> int32: ...


@overload
def f(x: int32) -> int32: ...
def f[U: P[list[int32]]](x: int32) -> int32:  # tpyc: error(/Type argument 'list\[int32\]' does not satisfy bound 'ValueType' for type parameter 'T' of 'P'/)
    return x

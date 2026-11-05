# An integer given to a protocol's type parameter is refused.
from typing import Protocol
from tpy import ValueType, int32


class P[T: ValueType](Protocol):
    def size(self) -> int32: ...


def use(p: P[3]) -> int32:  # tpyc: error(/Type parameter 'T' of 'P' requires a type, got integer 3/)
    return p.size()

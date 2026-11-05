# A bound broken only in a property setter's parameter is caught.
from typing import Protocol
from tpy import ValueType, int32


class P[T: ValueType](Protocol):
    def size(self) -> int32: ...


class R:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    @property
    def v(self) -> int32:
        return self.n

    @v.setter
    def v(self, x: P[list[int32]]) -> None:  # tpyc: error(/Type argument 'list\[int32\]' does not satisfy bound 'ValueType' for type parameter 'T' of 'P'/)
        self.n = x.size()

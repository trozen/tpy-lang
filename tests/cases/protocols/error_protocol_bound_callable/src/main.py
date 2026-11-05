# A bound broken inside a Callable parameter type.
from typing import Callable, Protocol
from tpy import ValueType, int32


class P[T: ValueType](Protocol):
    def size(self) -> int32: ...


def use(cb: Callable[[P[list[int32]]], None]) -> int32:  # tpyc: error(/Type argument 'list\[int32\]' does not satisfy bound 'ValueType' for type parameter 'T' of 'P'/)
    return 1

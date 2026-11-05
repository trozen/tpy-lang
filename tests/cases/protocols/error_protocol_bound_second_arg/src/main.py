# A two-parameter protocol whose second argument breaks its bound.
from typing import Protocol
from tpy import ValueType, int32


class P2[K, V: ValueType](Protocol):
    def size(self) -> int32: ...


def use(p: P2[int32, list[int32]]) -> int32:  # tpyc: error(/Type argument 'list\[int32\]' does not satisfy bound 'ValueType' for type parameter 'V' of 'P2'/)
    return p.size()

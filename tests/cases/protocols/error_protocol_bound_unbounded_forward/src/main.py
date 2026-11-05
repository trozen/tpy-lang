# An unbounded type parameter forwarded into a bounded protocol does not satisfy its bound.
from typing import Protocol
from tpy import ValueType, int32


class P[T: ValueType](Protocol):
    def size(self) -> int32: ...


def use[U](p: P[U]) -> int32:  # tpyc: error(/Type argument 'U' does not satisfy bound 'ValueType' for type parameter 'T' of 'P'/)
    return p.size()

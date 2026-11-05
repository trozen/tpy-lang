# A bound naming an earlier, unbounded sibling does not satisfy the bound it is given.
from typing import Protocol
from tpy import ValueType, int32


class Sized[T: ValueType](Protocol):
    def size(self) -> int32: ...


class Q[U, V: Sized[U]](Protocol):  # tpyc: error(/Type argument 'U' does not satisfy bound 'ValueType' for type parameter 'T' of 'Sized'/)
    def g(self) -> int32: ...

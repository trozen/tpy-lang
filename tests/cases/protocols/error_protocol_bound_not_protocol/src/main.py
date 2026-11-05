# A protocol type-parameter bound must be a protocol, as a record's must.
from typing import Protocol
from tpy import int32


class P[T: int32 | float](Protocol):  # tpyc: error(/Type parameter bound must be a protocol, got float \| int32/)
    def size(self) -> int32: ...

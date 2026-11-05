# A protocol cannot declare an integer type parameter yet.
from typing import Protocol
from tpy import int32


class P[N: int](Protocol):  # tpyc: error(/Protocol 'P': integer type parameters \(`N: int`\) are not yet supported/)
    def size(self) -> int32: ...

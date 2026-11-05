# A record's integer type parameter given to a protocol's type parameter is refused.
from typing import Protocol
from tpy import int32


class P[T](Protocol):
    def f(self) -> int32: ...


class R[N: int]:
    def use(self, p: P[N]) -> int32:  # tpyc: error(/Integer type parameter 'N' cannot be used as a type annotation/)
        return p.f()

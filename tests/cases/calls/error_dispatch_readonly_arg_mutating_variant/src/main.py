# A readonly argument whose best variant mutates it is rejected, never re-ranked
# to bare T (docs/OVERLOAD_DESIGN.md "A readonly argument does not re-rank").
from typing import Protocol
from tpy import dispatch, readonly, int32


class Named(Protocol):
    def bump(self, n: int32) -> None: ...


class A:
    hits: int32

    def __init__(self) -> None:
        self.hits = 0

    def bump(self, n: int32) -> None:
        self.hits += n


@dispatch
def gp[T](x: T) -> str:
    return "bare"


@dispatch
def gp(x: Named) -> str:
    x.bump(1)
    return "proto"


def ro(x: readonly[A]) -> str:
    return gp(x)  # tpyc: error(/Cannot pass readonly\[A\] as mutable Named/)


def main() -> None:
    a = A()
    print(ro(a), a.hits)


main()

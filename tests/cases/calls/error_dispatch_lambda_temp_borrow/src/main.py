# A lambda whose borrow roots in a temporary its body creates must not steer
# overload resolution: the temporary-borrow reject runs after every body is
# analyzed, so this @dispatch call keeps its "Ambiguous overload" verdict
# (itself a defect: BUGS.md#dispatch-callable-result-generic-ties-concrete).
from __future__ import annotations
from typing import Callable
from tpy import int32, Own, dispatch


class Inner:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Rec:
    n: int32
    inner: Inner

    def __init__(self, n: int32) -> None:
        self.n = n
        self.inner = Inner(n)

    def get_inner(self) -> Inner:
        return self.inner


def mk(r: Rec) -> Own[Rec]:
    return Rec(r.n * 10)


@dispatch
def apply[T](xs: list[Rec], f: Callable[[Rec], T]) -> int32:
    return len(xs)


@dispatch
def apply(xs: list[Rec], f: Callable[[Rec], Inner]) -> int32:
    return 2 * len(xs)


def main() -> None:
    # Annotated: an unannotated list local matches no candidate at all.
    rs: list[Rec] = [Rec(2), Rec(1)]
    # Both candidates fit the lambda; neither may be dropped by the reject.
    print(apply(rs, lambda r: mk(r).get_inner()))  # tpyc: error(/Ambiguous overload for 'apply'/)


main()

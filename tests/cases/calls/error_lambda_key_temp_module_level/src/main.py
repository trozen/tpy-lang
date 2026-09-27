# A module-level lambda whose borrow points into a value its body created is
# rejected, although module statements are analyzed before the method bodies
# whose borrow facts decide it. Returning it by value instead is
# BUGS.md#lambda-body-partial-return-checks.
from __future__ import annotations
from tpy import int32, Own


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


rs = [Rec(2), Rec(1), Rec(3)]
# get_inner() borrows the Rec that mk(r) creates inside the lambda.
for i in map(lambda r: mk(r).get_inner(), rs):  # tpyc: error(/Cannot return a borrow of a temporary from this lambda: its result points into 'mk\(\.\.\.\)'/)
    print(i.v)

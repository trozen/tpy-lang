# A lambda at a generic result slot whose borrow points into a value its own
# body created is rejected: the value dies when the lambda returns. Returning
# it by value instead is BUGS.md#lambda-body-partial-return-checks.
from __future__ import annotations
from tpy import int32, Own


class Inner:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def __lt__(self, o: Inner) -> bool:
        return self.v < o.v


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


def main() -> None:
    rs = [Rec(2), Rec(1), Rec(3)]
    # get_inner() borrows the Rec that mk(r) creates inside the lambda.
    ys = sorted(rs, key=lambda r: mk(r).get_inner())  # tpyc: error(/Cannot return a borrow of a temporary from this lambda: its result points into 'mk\(\.\.\.\)'/)
    print(len(ys))


main()

# A key lambda returning a borrow into a value its own body created returns
# it by value instead (its result form is owned): the borrowed part copies,
# warned, since the temporary it points into dies with the lambda.
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
    ys = sorted(rs, key=lambda r: mk(r).get_inner())  # tpyc: warning(/copies Inner into owned storage/)
    print(len(ys), [y.n for y in ys])


main()

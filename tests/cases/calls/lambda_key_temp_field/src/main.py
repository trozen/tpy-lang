# A key lambda reading a FIELD of a value its own body created returns it by
# value (its result form is owned): the field of the dying temporary moves
# out, so nothing is copied.
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
    # .inner is a field of the Rec that mk(r) creates inside the lambda.
    ys = sorted(rs, key=lambda r: mk(r).inner)  # tpyc: ok
    print(len(ys), [y.n for y in ys])


main()

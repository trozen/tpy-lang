# A @dispatch method argument is gated and rendered by the variant the call binds,
# not the first one declared. No CPython run: its dispatch stub cannot tell list[float] from list[int32].
from tpy import Own, int32, dispatch


class R:
    def __init__(self, n: int32) -> None:
        self.n = n


def mkr() -> Own[R]:
    return R(4)


class P:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    @dispatch
    def tk2(self, xs: list[float]) -> int32:
        return len(xs)

    @dispatch
    def tk2(self, xs: list[int32]) -> int32:
        xs.append(4)
        return len(xs)

    @dispatch
    def m(self, xs: list[float]) -> int32:
        xs.append(1.0)
        return len(xs)

    @dispatch
    def m(self, r: R) -> int32:
        self.n = r.n
        return r.n


def main() -> None:
    c = P()
    # read-only variant declared first: the literal binds the mutating int32 one.
    print("first-reads:", c.tk2([1, 2]))  # tpyc: ok
    # mutating variant declared first: rvalues bind the read-only record one.
    print("first-writes:", c.m(R(7)), c.m(mkr()), c.m([2.0]))  # tpyc: ok


main()

# The @dispatch generic fallback after a refused candidate: the first
# generic candidate is refused, the next one takes the call, and what its
# arguments decide (a pending list's cells, a consuming call) is decided
# once, at that winner.
from typing import Self
from tpy import Own, dispatch, int32, int64


class B:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def take(self: Own[Self]) -> Own[Self]:
        return self


def a64() -> int64:
    return 3000000000


def val(b: Own[B]) -> int64:  # tpyc: warning(/Own\[B\] param 'b' is never consumed/)
    return b.v


# Keyword arguments send a generic @dispatch set to the fallback.
@dispatch
def kw[T](xs: list[T], v: str) -> str:
    return "str"


@dispatch
def kw[T](xs: list[T], v: T) -> T:
    xs.append(v)  # tpyc: warning(/may copy T into owned storage/)
    return v


def main() -> None:
    # a pending list refused by the first candidate, settled by the next at int64
    ys = [1, 2]
    print("fallback-cells:", kw(xs=ys, v=a64()), ys)  # tpyc: ok
    # a consuming call consumed once, at the candidate that takes it
    b = B(5)
    zs = [1, 2]
    print("fallback-consume:", kw(xs=zs, v=val(b.take())), zs)  # tpyc: ok


main()

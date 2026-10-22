# A tuple local holding an owned element beside a borrowed one, returned into
# Own element slots: the owned (@nocopy) element moves, and the borrowed one
# copies with a warning.
from tpy import int32, Own, nocopy


@nocopy
class N:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def f(p: P) -> tuple[Own[N], Own[P]]:
    t = (N(1), p)
    # The return moves the N it owns and copies the borrowed P.
    return t  # tpyc: warning(/tuple element 1\)/)


def main() -> None:
    p = P(2)
    n, q = f(p)
    p.x = 5
    print(n.v, q.x, p.x)


main()

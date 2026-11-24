# A dying tuple local returned at a tuple of an `Own` union and a borrowed
# record is refused, never copied unwarned: the union element needs a move
# out of the tuple (BUGS.md#dying-owned-value-staged-result-copies).
from tpy import Own, int32


class A:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.xs = [n]


class B:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def rec_mixed(b: B) -> tuple[Own[A | int32], B]:
    t = (A(1), b)
    return t  # tpyc: error(/not yet supported/)


def main() -> None:
    b = B(2)
    a, ab = rec_mixed(b)
    if isinstance(a, A):
        print(len(a.xs), ab.n)


main()

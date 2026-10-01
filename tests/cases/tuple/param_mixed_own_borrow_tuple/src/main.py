# A mixed owned+borrow tuple param (tuple[Own[A], A] -- one Own element, one
# bare reference element aliasing the caller) takes the ownership-transfer
# `std::tuple<A, const A*>&&` ABI of its fully owned twin, the borrowed
# element a pointer; a body that only reads it warns never-consumed like the
# twin.
from tpy import Own, int32


class A:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def read_mixed(p: tuple[Own[A], A]) -> int32:  # tpyc: warning(/owned tuple param 'p' is never consumed/)
    return p[0].n + p[1].n


def main() -> None:
    keep = A(2)
    print(read_mixed((A(1), keep)))


main()

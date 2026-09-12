# A mixed owned+borrow tuple param (tuple[Own[A], A] -- one Own element, one
# bare reference element aliasing the caller) is excluded from the owned
# `std::tuple<...>&&` ABI and stays a plain const& borrow with no spurious
# never-consumed warning. Guards the exclusion; the Own element being received
# as a borrow rather than honored is a known per-element-ownership gap.
from tpy import Own, int32


class A:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def read_mixed(p: tuple[Own[A], A]) -> int32:  # tpyc: ok
    return p[0].n + p[1].n


def main() -> None:
    keep = A(2)
    print(read_mixed((A(1), keep)))


main()

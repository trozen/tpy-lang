# readonly[tuple] param normalization: an all-value-type tuple strips readonly
# (copy semantics), a mixed tuple keeps it and projects readonly only onto the
# reference element (@nocopy proves the element is borrowed, not copied).
from tpy import Int32, readonly, nocopy


@nocopy
class Counter:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


def value_only(p: readonly[tuple[Int32, Int32]]) -> Int32:
    return p[0] + p[1]


def mixed(p: readonly[tuple[Int32, Counter]]) -> Int32:
    a = p[0]  # tpyc: type(Int32)
    b = p[1]  # tpyc: type(/readonly/)
    return a + b.n


def main() -> None:
    print("value_only:", value_only((3, 4)))
    c = Counter(5)
    print("mixed:", mixed((10, c)))


main()

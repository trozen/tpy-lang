# An `Own[tuple]` parameter is STORAGE form, so `std::get` already yields the
# element reference; the deref-flagged alias render would be ill-formed.
from tpy import Int32, Own, nocopy


@nocopy
class Counter:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


def use(p: Own[tuple[Int32, Counter]]) -> Int32:
    b = p[1]  # tpyc: error(/decl.slot_type/)
    return b.n


def main() -> None:
    print(use((1, Counter(5))))


main()

# An `Own[tuple]` parameter is STORAGE form, so `std::get` already yields the
# element reference; the deref-flagged alias render would be ill-formed.
from tpy import int32, Own, nocopy


@nocopy
class Counter:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def use(p: Own[tuple[int32, Counter]]) -> int32:
    b = p[1]  # tpyc: error(/decl.slot_type/)
    return b.n


def main() -> None:
    print(use((1, Counter(5))))


main()

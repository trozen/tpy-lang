# A user `__getitem__` that returns by VALUE is not a chain hop: the unpack
# would lift element pointers out of the accessor's temporary, which dies at
# the end of the statement. The reference-returning sibling is a hop and
# compiles (`tuple/unpack_chained_receiver`, section `ref_getitem_hop`). The
# rule is that every hop must name storage the root outlives
# (`docs/LANGUAGE_FEATURES.md`, const-reference parameter passing); the four
# temporary-minting hops it rejects are BUGS.md#chain-temporary-hop-rejected.
from tpy import int32, Own


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Holder:
    pair: tuple[int32, Box]

    def __init__(self, n: int32) -> None:
        self.pair = (n, Box(n * 2))


class Store:
    k: int32

    def __init__(self, k: int32) -> None:
        self.k = k

    def __getitem__(self, i: int32) -> Own[Holder]:
        return Holder(self.k + i)


def unpack(s: Store) -> int32:
    a, b = s[0].pair  # tpyc: error(/stmt.tuple_unpack/)
    b.n += 5
    return a + b.n


def main() -> None:
    print(unpack(Store(3)))


main()

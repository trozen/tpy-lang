# An Own-returning @property is not a chain hop: the unpack would lift element
# pointers out of the getter's temporary. The reference-returning sibling IS a
# hop and compiles (`tuple/unpack_chained_receiver`, section
# `ref_property_hop`); the by-value `__getitem__` twin is
# `tuple/error_value_getitem_recv_unpack`. The rule is that every hop must name
# storage the root outlives (`docs/LANGUAGE_FEATURES.md`, const-reference
# parameter passing); the four temporary-minting hops it rejects are
# BUGS.md#chain-temporary-hop-rejected.
from tpy import int32, Own


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Holder:
    pair: tuple[int32, Box]

    def __init__(self, n: int32) -> None:
        self.pair = (n, Box(n * 2))


class Owner:
    k: int32

    def __init__(self, k: int32) -> None:
        self.k = k

    @property
    def made(self) -> Own[Holder]:
        return Holder(self.k)


def unpack(o: Owner) -> int32:
    a, b = o.made.pair  # tpyc: error(/stmt.tuple_unpack/)
    b.n += 5
    return a + b.n


def main() -> None:
    print(unpack(Owner(3)))


main()

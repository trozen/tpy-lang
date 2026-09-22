# A tuple-of-references global is a tuple of pointer slots bound once at
# module init; rebinding it from a function body is refused as the scalar
# reference global's rebind is (a slot aimed at a function's storage would
# dangle). The assign form, the most representative position.
from tpy import int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


V = Box(1)
W = Box(2)
pair_g: tuple[Box, int32] = (V, 1)


def rebind() -> None:
    global pair_g
    pair_g = (W, 2)  # tpyc: error(/Cannot reassign global variable 'pair_g' of a tuple of reference types/)


def main() -> None:
    rebind()
    print(pair_g[0].n)


main()

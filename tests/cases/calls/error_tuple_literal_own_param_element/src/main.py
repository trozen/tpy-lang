# A tuple literal at a native protocol slot with an `Own` parameter element:
# the element is a move source at a slot that borrows. The parameter is only
# hashed, so it also draws the unconsumed-Own warning.
from tpy import int32, Own, uint64


class Box:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v

    def __hash__(self) -> uint64:
        return uint64(self.val)


def h(b: Own[Box]) -> uint64:
    # The Own parameter is a tuple-literal element here.
    return hash((1, b))  # tpyc: error(/expr\.tuple_literal/)


def main() -> None:
    print(h(Box(1)) == 0)


main()

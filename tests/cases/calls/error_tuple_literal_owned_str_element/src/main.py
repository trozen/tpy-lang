# A tuple literal at a native protocol slot with an OWNED-view element: the
# str element would render owned in the borrow form and bare otherwise, so
# the whole literal is refused rather than half-mirrored.
from tpy import Int32, UInt64


class Box:
    val: Int32

    def __init__(self, v: Int32) -> None:
        self.val = v

    def __hash__(self) -> UInt64:
        return UInt64(self.val)


def h(b: Box, s: str) -> UInt64:
    # The tuple literal mixes an owned view with a record borrow.
    return hash((s, b))  # tpyc: error(/expr\.tuple_literal/)


def main() -> None:
    print(h(Box(1), "x") == 0)


main()

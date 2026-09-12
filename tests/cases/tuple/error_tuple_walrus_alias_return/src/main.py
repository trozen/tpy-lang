# A walrus binding from local container storage aliases the element like a
# VarDecl binding (borrow registration), so returning it must be rejected:
# the lifted pointers would dangle with the list.
from tpy import int32


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


def ret() -> tuple[int32, Box]:
    items: list[tuple[int32, Box]] = [(1, Box(5))]
    if (t := items[0])[0] > 0:
        return t  # tpyc: error(/storage owned by the function/)
    raise RuntimeError("unreachable")


def main() -> None:
    pass


main()

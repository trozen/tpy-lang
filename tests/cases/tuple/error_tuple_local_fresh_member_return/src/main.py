# Same hazard at a return boundary: a tuple local owning a fresh Box would
# dangle when returned by borrow form. Rejected (the C++ build would otherwise
# fail with a tuple ctor mismatch), pointing at Own[Box].
from tpy import Int32


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


def make() -> tuple[Int32, Box]:
    t = (Int32(1), Box(Int32(7)))
    return t  # tpyc: error(/owns a freshly constructed value/)


def main() -> None:
    pair = make()
    print(pair[0], pair[1].val)


main()

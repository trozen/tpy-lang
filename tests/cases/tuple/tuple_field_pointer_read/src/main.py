# Storage->borrow round-trip through a tuple FIELD: the field stores the
# value form (std::tuple<Int32, Box>), reading it into a borrow-tuple param
# goes through tuple_to_pointer's value->T* (addressof) branch, and mutating
# through the borrow reaches the field's element.
from tpy import Int32


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


class Holder:
    pair: tuple[Int32, Box]
    def __init__(self, b: Box) -> None:
        self.pair = (1, b)


def bump(t: tuple[Int32, Box]) -> None:
    t[1].val = t[1].val + 10


def main() -> None:
    h = Holder(Box(5))
    bump(h.pair)
    print(h.pair[1].val)
    bump(h.pair)
    print(h.pair[1].val)


main()

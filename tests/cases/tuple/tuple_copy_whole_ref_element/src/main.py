# copy() of a WHOLE tuple with a reference element. The copy is built in storage
# form directly (element-wise value copy), not via a borrow-form literal +
# tuple_to_storage (which broke on a const source / spelled a malformed tuple
# type). Both a field-store and a local binding are covered. Copy semantics are
# intended here (the tuple's Box element is stored by value).
from tpy import copy, int32


class Box:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v


class Holder:
    pair: tuple[int32, Box]

    def __init__(self, b: Box) -> None:
        self.pair = copy((77, b))


def main() -> None:
    b = Box(5)
    h = Holder(b)
    print(h.pair[0])
    print(h.pair[1].val)
    t = copy((1, b))
    print(t[0] + t[1].val)


main()

# Rebinding a MIXED tuple local to an all-borrow STORAGE source (a field):
# the rebound local is the pointer tuple, so the rebind re-points both
# elements at the field's objects -- no copy, nothing warned -- and a write
# through the local reaches the field, as CPython aliases.
from tpy import int32, Own, copy


class Box:
    val: int32

    def __init__(self, val: int32) -> None:
        self.val = val


class Holder:
    pair: tuple[Box, Box]

    def __init__(self, b: Box) -> None:
        self.pair = (copy(b), copy(b))


def make_mixed(b: Box) -> tuple[Own[Box], Box]:
    return (Box(1), b)


def realias(h: Holder, b: Box) -> None:
    p = make_mixed(b)
    p = h.pair  # tpyc: ok
    p[0].val = 55
    p[1].val = 66


def main() -> None:
    b = Box(7)
    h = Holder(b)
    realias(h, b)
    print("elem0:", h.pair[0].val)
    print("elem1:", h.pair[1].val)


main()

# A borrow-tuple LITERAL declaration later rebound to a field read. The rebound
# name aliases the field's tuple, so the write through it is visible on the
# field afterwards. Storing the record INTO the field copies it (the warned
# storage-form store), which is why the observation is made on the field.
from tpy import int32


class Box:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v


class Holder:
    pair: tuple[int32, Box]

    def __init__(self, b: Box) -> None:
        self.pair = (7, b)  # tpyc: warning(/copies Box into field/)


def use(h: Holder, b: Box) -> int32:
    t = (1, b)  # the borrow-tuple literal declaration
    first = t[1].val
    t = h.pair
    t[1].val = 42  # writes through the rebound alias
    return first


def main() -> None:
    h = Holder(Box(5))
    first = use(h, Box(3))
    print(first, h.pair[1].val)


main()

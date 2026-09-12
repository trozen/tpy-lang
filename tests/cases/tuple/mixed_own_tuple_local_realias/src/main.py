# Rebinding a MIXED tuple local to an all-borrow STORAGE source (a field) has
# no aliasing form to take: the local's owned element is by value, so the
# field's element 0 is copied into it while element 1 still aliases. That is an
# acknowledged divergence -- CPython aliases both -- so it warns at the rebind
# and offers copy() as the acknowledgement. no_cpython for that reason: the
# divergence is the thing under test.
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
    p = h.pair  # tpyc: warning(/copies tuple\[Box, Box\] into owned storage/)
    p[0].val = 55
    p[1].val = 66


def main() -> None:
    b = Box(7)
    h = Holder(b)
    realias(h, b)
    # CPython prints 55 66; element 0 was copied into the local's owned slot.
    print("elem0:", h.pair[0].val)
    print("elem1:", h.pair[1].val)


main()

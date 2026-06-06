# Returning a borrow-form tuple read from a durable storage root (param
# field, directly or through a local alias) lifts element addresses into
# that storage: the caller's mutation through the returned tuple reaches
# the field, like CPython aliasing.
from tpy import Int32


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


class Holder:
    pair: tuple[Int32, Box]
    def __init__(self, b: Box) -> None:
        self.pair = (1, b)


def ret_field(h: Holder) -> tuple[Int32, Box]:
    return h.pair


def ret_alias(h: Holder) -> tuple[Int32, Box]:
    t = h.pair
    return t


def main() -> None:
    h = Holder(Box(5))
    t = ret_field(h)
    t[1].val = 99
    print(h.pair[1].val)

    h2 = Holder(Box(7))
    u = ret_alias(h2)
    u[1].val = 42
    print(h2.pair[1].val)


main()

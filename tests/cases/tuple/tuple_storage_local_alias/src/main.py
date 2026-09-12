# A tuple local bound from an lvalue storage source (list element, field)
# ALIASES the source like CPython: mutation through the local reaches the
# stored element; reads through both views agree.
from tpy import int32


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


def from_list() -> None:
    items: list[tuple[int32, Box]] = [(1, Box(5))]
    t = items[0]
    t[1].val = 99
    print(items[0][1].val)


class Holder:
    pair: tuple[int32, Box]
    def __init__(self, b: Box) -> None:
        self.pair = (1, b)


def from_field() -> None:
    h = Holder(Box(7))
    t = h.pair
    t[1].val = 42
    print(h.pair[1].val)


def chain_alias() -> None:
    items: list[tuple[int32, Box]] = [(1, Box(3))]
    t = items[0]
    u = t
    u[1].val = 11
    print(items[0][1].val)


def main() -> None:
    from_list()
    from_field()
    chain_alias()


main()

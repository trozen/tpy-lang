# Rebinding an ephemeral-alias tuple local to DURABLE storage clears the
# ephemeral fact and retargets the borrow, so the return is sound: the
# local keeps the borrow C++ shape across rebinds (first init was borrow
# form), and the storage RHS takes the element-wise lift.
from typing import Iterator
from tpy import int32


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


class Holder:
    pair: tuple[int32, Box]
    def __init__(self, b: Box) -> None:
        self.pair = (44, b)


def gen() -> Iterator[tuple[int32, Box]]:
    items: list[tuple[int32, Box]] = [(1, Box(5))]
    yield items[0]


def pick(h: Holder) -> tuple[int32, Box]:
    for p in gen():
        q = p
        q = h.pair
        return q
    raise RuntimeError("empty")


def main() -> None:
    h = Holder(Box(7))
    t = pick(h)
    t[1].val = 99
    print(t[0])
    print(h.pair[1].val)


main()

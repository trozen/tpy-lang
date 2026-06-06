# Ephemerality flows through a ternary binding (the result reads whichever
# arm is taken), so `q = p if c else p` of an ephemeral tuple loop var is
# the same stale-slot borrow and its return is rejected.
from typing import Iterator
from tpy import Int32


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


def gen() -> Iterator[tuple[Int32, Box]]:
    items: list[tuple[Int32, Box]] = [(1, Box(5))]
    yield items[0]


def take(c: bool) -> tuple[Int32, Box]:
    for p in gen():
        q = p if c else p
        return q  # tpyc: error(/borrows an element/)
    raise RuntimeError("empty")


def main() -> None:
    pass


main()

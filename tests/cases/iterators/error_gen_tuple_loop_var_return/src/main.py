# A tuple loop var over a generator borrows the generator's frame storage;
# returning it lets the borrow outlive the frame (use-after-free), so the
# escape is rejected like the scalar ephemeral-borrow case.
from typing import Iterator
from tpy import Int32


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


def gen() -> Iterator[tuple[Int32, Box]]:
    items: list[tuple[Int32, Box]] = [(1, Box(5))]
    yield items[0]


def take() -> tuple[Int32, Box]:
    for p in gen():
        return p  # tpyc: error(/borrows an element/)
    raise RuntimeError("empty")


def main() -> None:
    pass


main()

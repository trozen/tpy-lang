# A relay generator may re-yield a tuple loop var borrowed from an inner
# generator (delegation: the inner generator is materialized in the relay's
# frame and lives for the relay's whole lifetime). The borrow stays an
# alias: mutating through the relayed tuple on one iteration is visible on
# the next yield of the same element.
from typing import Iterator
from tpy import Int32


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


def gen() -> Iterator[tuple[Int32, Box]]:
    items: list[tuple[Int32, Box]] = [(1, Box(5))]
    for _ in range(2):
        yield items[0]


def relay() -> Iterator[tuple[Int32, Box]]:
    for p in gen():
        yield p


def main() -> None:
    first = True
    for q in relay():
        if first:
            q[1].val = 99
            first = False
        else:
            print(q[1].val)


main()

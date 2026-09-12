# A ternary where only one arm is a durable-member tuple local (the other is a
# param tuple). cond=False selects t (member b), so mutating the yielded element
# is visible in b -- the alias holds through the ternary.
from typing import Iterator
from tpy import int32


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


def gen(p: tuple[int32, Box], b: Box, cond: bool) -> Iterator[tuple[int32, Box]]:
    t = (1, b)
    u = p if cond else t
    yield u


def main() -> None:
    shared = Box(5)
    other = Box(0)
    for pair in gen((0, other), shared, False):
        pair[1].val = 99
    print(shared.val)


main()

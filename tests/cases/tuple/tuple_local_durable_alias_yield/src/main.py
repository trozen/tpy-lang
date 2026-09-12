# A durable reference member shared through a local-to-local alias (u = t):
# yielding the alias now aliases the member, so a post-boundary mutation reaches
# the caller's object.
from typing import Iterator
from tpy import int32


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


def gen(b: Box) -> Iterator[tuple[int32, Box]]:
    t = (1, b)
    u = t
    yield u


def main() -> None:
    shared = Box(5)
    for pair in gen(shared):
        pair[1].val = 99
    print(shared.val)


main()

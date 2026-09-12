# A self-assignment (t = t) preserves the alias: the durable member is still
# shared, so yielding t and mutating the element reaches the caller's object.
from typing import Iterator
from tpy import int32


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


def gen(b: Box) -> Iterator[tuple[int32, Box]]:
    t = (1, b)
    t = t
    yield t


def main() -> None:
    shared = Box(5)
    for pair in gen(shared):
        pair[1].val = 99
    print(shared.val)


main()

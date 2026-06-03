# The escape the durable-member diagnostic points to: yielding the tuple literal
# directly keeps borrow form and SHARES the member (mutation propagates back).
from typing import Iterator
from tpy import Int32


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


def gen(b: Box) -> Iterator[tuple[Int32, Box]]:
    yield (1, b)


def main() -> None:
    shared = Box(Int32(5))
    for pair in gen(shared):
        pair[1].val = 99   # mutate through the shared (borrow-form) member
    print(shared.val)      # shares -> 99 (would be 5 if it copied)


main()

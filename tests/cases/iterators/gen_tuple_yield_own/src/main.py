# The Own[Box] element form is the declaration-driven fix for a fresh non-value
# tuple member: the member moves out of the frame each iteration instead of
# being borrowed, so each yielded Box is a distinct owned object (no dangle).
from typing import Iterator
from tpy import Own


class Box:
    val: int

    def __init__(self, v: int):
        self.val = v


def g(n: int) -> Iterator[tuple[int, Own[Box]]]:
    for i in range(n):
        yield (i, Box(i * 10))


def main() -> None:
    total = 0
    for i, b in g(3):
        total = total + i + b.val
    print(total)


main()

# A fresh non-value member at tuple index 0 (and a second fresh member) must be
# rejected too -- guards the element-index in the per-member diagnostic.
from typing import Iterator


class Box:
    val: int

    def __init__(self, v: int):
        self.val = v


def g(n: int) -> Iterator[tuple[Box, Box]]:
    for i in range(n):
        yield (Box(i), Box(i))  # tpyc: error(/Cannot yield local.*tuple element 0.*Own\[Box\]/)


def main() -> None:
    for a, b in g(3):
        print(a.val + b.val)


main()

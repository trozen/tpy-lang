# A readonly-wrapped tuple yield still uses the per-element borrow slot, so a
# fresh non-value member is rejected (the gate unwraps the readonly wrapper).
from typing import Iterator
from tpy import readonly


class Box:
    val: int

    def __init__(self, v: int):
        self.val = v


def g(n: int) -> Iterator[readonly[tuple[int, Box]]]:
    for i in range(n):
        yield (i, Box(i))  # tpyc: error(/Cannot yield local.*tuple element 1.*Own\[Box\]/)


def main() -> None:
    for t in g(3):
        print(t[1].val)


main()

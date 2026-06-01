# A nested tuple whose inner member is Own moves out per-iteration (value-stored,
# not a borrow slot), so the nested-borrow restriction does not apply.
from typing import Iterator
from tpy import Own


class Box:
    val: int

    def __init__(self, v: int):
        self.val = v


def g(n: int) -> Iterator[tuple[int, tuple[int, Own[Box]]]]:
    for i in range(n):
        yield (i, (i, Box(i * 5)))


def main() -> None:
    total = 0
    for i, pair in g(3):
        total = total + pair[1].val
    print(total)


main()

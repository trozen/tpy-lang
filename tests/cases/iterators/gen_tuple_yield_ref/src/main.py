# Borrow ABI for a tuple yield: the non-value member is a live reference to the
# source element, not a copy. Mutating through the yielded element must be
# visible in the source list (CPython-faithful) -- a read-only test could not
# distinguish a reference from a copy.
from typing import Iterator


class Box:
    val: int

    def __init__(self, v: int):
        self.val = v


def g(boxes: list[Box]) -> Iterator[tuple[int, Box]]:
    i = 0
    for b in boxes:
        yield (i, b)
        i += 1


def main() -> None:
    data = [Box(1), Box(2), Box(3)]
    for i, b in g(data):
        b.val = b.val + 100
    for box in data:
        print(box.val)


main()

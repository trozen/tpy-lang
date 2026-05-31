# Iterator[Own[T]] yields freshly-constructed owned values (moved out of the
# frame, not borrowed). Each iteration produces a distinct object, so a consumer
# may keep them -- the owned ABI is the declaration-driven counterpart to the
# borrow ABI.
from typing import Iterator
from tpy import Own


class Node:
    val: int

    def __init__(self, v: int):
        self.val = v


def boxes(n: int) -> Iterator[Own[Node]]:
    for i in range(n):
        yield Node(i)


def main() -> None:
    total = 0
    for b in boxes(4):
        total = total + b.val
    print(total)


main()

# Error (consumer-side ephemeral borrow): returning a borrow consumed from a
# generator past its iteration step would dangle (the frame is gone after the
# consumer returns). The loop var is an ephemeral borrow, so returning it is
# rejected -- copy it out first.
from typing import Iterator


class Node:
    val: int

    def __init__(self, v: int):
        self.val = v


def each(xs: list[Node]) -> Iterator[Node]:
    for b in xs:
        yield b


def first(xs: list[Node]) -> Node:
    for b in each(xs):
        return b  # tpyc: error(/Cannot return 'b'.*current iteration step/)
    return xs[0]


def main() -> None:
    print(first([Node(5)]).val)


main()

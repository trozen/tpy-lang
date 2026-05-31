# Error (consumer-side ephemeral borrow): re-yielding a borrow consumed from an
# inner generator past its iteration step would let the outer consumer read a
# stale frame slot. The inner loop var is an ephemeral borrow, so yielding it
# onward is rejected -- copy it out (or have the inner generator yield Own[T]).
from typing import Iterator


class Node:
    val: int

    def __init__(self, v: int):
        self.val = v


def inner(xs: list[Node]) -> Iterator[Node]:
    for b in xs:
        yield b


def outer(xs: list[Node]) -> Iterator[Node]:
    for b in inner(xs):
        yield b  # tpyc: error(/Cannot yield 'b'.*current iteration step/)


def main() -> None:
    data = [Node(1)]
    for n in outer(data):
        print(n.val)


main()

# Resumable-path generator iterating a list of reference-type elements with a
# `yield` inside the loop (Phase D2). The loop var aliases the container
# element as `T*` and must stay valid across the suspension -- exercised by
# reading `it` again on the second yield of each iteration.
from typing import Iterator
from tpy import Int32


class Node:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


def doubled(items: list[Node]) -> Iterator[Int32]:
    for it in items:
        yield it.x
        yield it.x + 100


def main() -> None:
    data = [Node(1), Node(2), Node(3)]
    for v in doubled(data):
        print(v)


main()

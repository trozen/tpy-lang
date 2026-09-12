# `filter` lends for any argument that HAS begin()/end(), but a generator frame
# does not: it reaches the `__next__` flavor, whose element comes back by value,
# so this rejects for reference elements even though the sibling
# `filter(pos, ns)` over a container routes
# (BUGS.md#nested-combinator-yields-element-copies).
from typing import Iterator

from tpy import int32


class Node:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def bump(self, d: int32) -> int32:
        self.v += d
        return self.v


def pos(n: Node) -> bool:
    return n.v > 0


def each(ns: list[Node]) -> Iterator[Node]:
    for n in ns:
        yield n


def main() -> None:
    ns = [Node(1), Node(2)]
    print([n.bump(1) for n in filter(pos, each(ns))])  # tpyc: error(/expr.list_comp/)


main()

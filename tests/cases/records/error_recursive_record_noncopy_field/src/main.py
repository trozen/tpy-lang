# A recursive record carrying a @nocopy field (Box) stays non-copyable: copying a
# borrowed instance into its own list[Self] must still be rejected, proving the
# cycle guard returns the real answer for the non-cyclic field, not a blanket
# "copyable" at the recursion point.
from __future__ import annotations
from tpy import Int32, Own
from tplib import Box


class Node:
    children: list[Node]
    tag: Box[Int32]

    def __init__(self, tag: Own[Box[Int32]]) -> None:
        self.children = []
        self.tag = tag


def link(parent: Node, child: Node) -> None:
    parent.children.append(child)  # tpyc: error(/cannot copy non-copyable type 'Node'/)


def main() -> None:
    a = Node(Box(1))
    b = Node(Box(2))
    link(a, b)
    print(len(a.children))


main()

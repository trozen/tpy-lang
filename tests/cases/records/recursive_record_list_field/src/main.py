# A copyable record with a list-of-itself field (a tree node) must compile --
# the copyability query must terminate on the self-reference, not recurse forever.
# Reads/mutations go through the container (never the original handle) so the
# warned value-into-container copy stays CPython-parity.
from __future__ import annotations
from tpy import Int32


class Node:
    val: Int32
    children: list[Node]

    def __init__(self, val: Int32) -> None:
        self.val = val
        self.children = []


def add(parent: Node, child: Node) -> None:
    parent.children.append(child)  # tpyc: warning(/copies Node into owned storage/)


def main() -> None:
    root = Node(1)
    leaf = Node(2)
    add(root, leaf)
    add(root, Node(3))
    root.children[0].val = 20
    root.children[0].children.append(Node(99))
    total: int = root.val
    for c in root.children:
        total += c.val
    print(root.val, root.children[0].val, len(root.children),
          len(root.children[0].children), total)


main()

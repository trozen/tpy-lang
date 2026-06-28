# A record with a dict[str, Self] field exercises the copyability guard's
# two-arg type walk (the value type cycles back) -- the sibling container path
# to recursive_record_list_field. Reads/mutations go through the stored map so
# the value-into-container copy stays CPython-parity.
from __future__ import annotations
from tpy import Int32


class Node:
    val: Int32
    kids: dict[str, Node]

    def __init__(self, val: Int32) -> None:
        self.val = val
        self.kids = {}


def put(parent: Node, key: str, child: Node) -> None:
    parent.kids[key] = child  # tpyc: warning(/copies Node into container/)


def main() -> None:
    root = Node(1)
    leaf = Node(2)
    put(root, "a", leaf)
    root.kids["a"].val = 20
    root.kids["a"].kids["b"] = Node(99)
    print(root.val, root.kids["a"].val, len(root.kids),
          len(root.kids["a"].kids))


main()

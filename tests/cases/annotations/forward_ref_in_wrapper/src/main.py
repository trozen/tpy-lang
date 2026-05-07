# Forward-ref string inside a structural wrapper: `list["Tree"]` and
# `Optional["Tree"]` recurse from `_parse_subscript_type_ref` back into
# the new string-Constant branch on each slice. Recursive data structures
# are the canonical real-world use of forward refs.
from tpy import Int32, Own
from typing import Optional


class Tree:
    value: Int32
    parent_value: Optional["Int32"]
    children: list["Tree"]

    def __init__(self, value: Int32, parent_value: Optional["Int32"]) -> None:
        self.value = value
        self.parent_value = parent_value
        self.children = []


def total(t: "Tree") -> Int32:
    s = t.value
    for c in t.children:
        s = s + total(c)
    return s


def main() -> None:
    root = Tree(Int32(10), None)
    root.children.append(Tree(Int32(1), Int32(10)))
    root.children.append(Tree(Int32(2), Int32(10)))
    print(total(root))


main()

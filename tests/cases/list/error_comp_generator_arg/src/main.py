# A combinator source with a GENERATOR-produced argument rejects for reference
# elements: the frame is an rvalue, so `zip` takes its owning factory and copies
# each element out of the tuple instead of lending it
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


def each(ns: list[Node]) -> Iterator[Node]:
    for n in ns:
        yield n


def main() -> None:
    ns = [Node(1), Node(2)]
    xs = [10, 20]
    print([n.bump(x) for n, x in zip(each(ns), xs)])  # tpyc: error(/expr.list_comp/)


main()

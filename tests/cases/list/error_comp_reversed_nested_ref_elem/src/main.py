# A comprehension whose source NESTS `reversed()` inside an admitted combinator
# rejects too: the outermost callee says nothing about what it wraps, and
# reversed_iter still hands back element COPIES
# (BUGS.md#reversed-yields-element-copies).
from tpy import Int32


class Node:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v

    def bump(self, d: Int32) -> Int32:
        self.v += d
        return self.v


def main() -> None:
    ns = [Node(1), Node(2)]
    xs = [10, 20]
    print([n.bump(x) for n, x in zip(reversed(ns), xs)])  # tpyc: error(/expr.list_comp/)


main()

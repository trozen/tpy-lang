# A comprehension over `reversed()` of REFERENCE elements rejects: the runtime's
# reversed_iter hands back element COPIES where every other combinator lends a
# reference (BUGS.md#reversed-yields-element-copies), so a mutation through the
# loop var would never reach the source. The value-element spelling stays
# admitted (tests/cases/list/comp_combinator_source).
from tpy import Int32


class Node:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v

    def bump(self) -> Int32:
        self.v += 1
        return self.v


def main() -> None:
    ns = [Node(1), Node(2)]
    print([n.bump() for n in reversed(ns)])  # tpyc: error(/expr.list_comp/)


main()

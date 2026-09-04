# A comprehension nesting one reference-lending combinator inside `zip` rejects
# for reference elements: an iterator argument is an rvalue, so `zip` picks its
# owning factory, whose tuple members are spelled by value where the direct
# factory spells them by reference -- the mutation would be lost even though
# `filter` alone lends, and even though the same nesting under `map`/`filter`
# routes (tests/cases/list/comp_combinator_source).
from tpy import Int32


class Node:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v

    def bump(self, d: Int32) -> Int32:
        self.v += d
        return self.v


def pos(n: Node) -> bool:
    return n.v > 0


def main() -> None:
    ns = [Node(1), Node(2)]
    xs = [10, 20]
    print([n.bump(x) for n, x in zip(filter(pos, ns), xs)])  # tpyc: error(/expr.list_comp/)


main()

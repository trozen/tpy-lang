# `filter` over another combinator rejects for reference elements: the runtime's
# has_begin_end excludes every next_iter_mixin iterator, so a combinator argument
# always lands on the by-value `owning_filter_iter` flavor and the mutation is
# lost (BUGS.md#nested-combinator-yields-element-copies). The sibling
# `filter(pos, us)` over the container routes
# (tests/cases/list/comp_combinator_source).
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


def main() -> None:
    us = [Node(11), Node(12)]
    print([m.bump(10) for m in filter(pos, filter(pos, us))])  # tpyc: error(/expr.list_comp/)


main()

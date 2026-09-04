# A SAFE OVER-REJECT: `map` over a `filter` result does lend at runtime, but only
# because codegen spells map's element `val_or_ref<T>` -- the argument itself has
# no begin()/end(), which is what every other combinator's lending flavor needs.
# The fence admits a combinator argument nowhere rather than carve out the one
# spelling whose safety comes from a different mechanism
# (BUGS.md#nested-combinator-yields-element-copies).
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


def same(n: Node) -> Node:
    return n


def main() -> None:
    us = [Node(11), Node(12)]
    print([m.bump(10) for m in map(same, filter(pos, us))])  # tpyc: error(/expr.list_comp/)


main()

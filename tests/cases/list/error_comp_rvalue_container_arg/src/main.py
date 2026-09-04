# A combinator source with an RVALUE container argument rejects for reference
# elements: `zip` iterates by reference only through its direct-iteration
# factory, which needs every argument to be an lvalue, so a list literal (or a
# `range()`, same shape) drops the reference on the OTHER argument too and the
# mutation would be lost (BUGS.md#nested-combinator-yields-element-copies).
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
    print([n.bump(x) for n, x in zip(ns, [10, 20])])  # tpyc: error(/expr.list_comp/)


main()

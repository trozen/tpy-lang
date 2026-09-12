# A `zip` source with an OWNED-container-returning call argument rejects for
# reference elements: `zip` is the one combinator with no OWNING direct-iteration
# flavor, so an rvalue argument leaves only the by-value tuple. `enumerate` has
# one and routes the same spelling (tests/cases/list/comp_combinator_source)
# (BUGS.md#nested-combinator-yields-element-copies).
from tpy import int32, Own


class Node:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def bump(self, d: int32) -> int32:
        self.v += d
        return self.v


def mk() -> Own[list[Node]]:
    return [Node(1), Node(2)]


def main() -> None:
    ns = [Node(3), Node(4)]
    print([n.bump(x.v) for n, x in zip(mk(), ns)])  # tpyc: error(/expr.list_comp/)


main()

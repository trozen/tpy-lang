# Comprehension over @nocopy elements in return position. The std::move
# on the trailing __result of the comprehension's stmt-expr matters most
# here -- the consumer is the function's return slot, which would otherwise
# copy-construct a vector of @nocopy Rc[Node].
#
# Note: source list is constructed locally rather than passed in. Borrowed
# `src: list[Rc[Node]]` from a parameter is currently const-propagated, so
# `x.clone()` on the iter element fails -- the comprehension path doesn't
# yet propagate loop-var mutation back to the source container the way the
# for-loop sema does. That's a separate gap.
from tpy import Int32, Own
from tplib import Rc


class Node:
    value: Int32

    def __init__(self, v: Int32) -> None:
        self.value = v


def build_clones() -> Own[list[Rc[Node]]]:
    src: list[Rc[Node]] = [
        Rc.new(Node(7)),
        Rc.new(Node(11)),
    ]
    return [x.clone() for x in src]


def main() -> None:
    out = build_clones()
    for c in out:
        print(c.get().value)


main()

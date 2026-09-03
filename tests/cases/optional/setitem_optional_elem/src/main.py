# Writing an element of an Optional-element container: a scalar or None into
# a value-repr `Int32 | None` slot, and None into a pointer-repr
# `Node | None` slot, over list and dict receivers.
from tpy import Int32


class Node:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


def main() -> None:
    xs: list[Int32 | None] = [None, None]
    xs[0] = 5  # a bare scalar into the value-repr optional slot
    n = 3
    xs[1] = n  # tpyc: ok
    a = xs[0]
    b = xs[1]
    if a is not None and b is not None:
        print(a + b)
    xs[1] = None  # tpyc: ok
    print(xs[1] is None)

    nodes: list[Node | None] = [Node(1), Node(2)]
    # A live alias of THIS element would not be invalidated by the write
    # (BUGS.md#setitem-write-under-live-element-borrow), so `kept` below
    # deliberately aliases a DIFFERENT element.
    nodes[1] = None  # clearing a pointer-repr optional element
    kept = nodes[0]
    if kept is not None:
        # The surviving element is ALIASED, not copied: the mutation here
        # must be visible when the element is read back below.
        kept.n = 42
    head = nodes[0]
    if head is not None:
        print(head.n, nodes[1] is None)

    d: dict[str, Int32 | None] = {}
    d["a"] = 5  # tpyc: ok
    d["b"] = None  # tpyc: ok
    got = d["a"]
    if got is not None:
        print(got + 1, d["b"] is None)

    recs: dict[str, Node | None] = {}
    recs["r"] = None  # tpyc: ok
    print(recs["r"] is None)


main()

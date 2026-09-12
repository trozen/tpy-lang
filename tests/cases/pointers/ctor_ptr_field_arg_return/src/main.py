# Returning a record ctor rvalue whose arg is a Ptr[T] field read
# (`return Handle(h.p)`). Mutating through the returned handle's pointer and
# reading the original proves the pointer aliases the same node, not a copy.
from tpy import int32, Ptr, Own


class Node:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Handle:
    p: Ptr[Node]

    def __init__(self, p: Ptr[Node]) -> None:
        self.p = p


def addr(n: Node) -> Ptr[Node]:
    return n


def rewrap(h: Handle) -> Own[Handle]:
    return Handle(h.p)


def make_handle(n: Node) -> Own[Handle]:
    h = Handle(addr(n))
    return rewrap(h)


def main() -> None:
    n = Node(5)
    h = make_handle(n)
    print(h.p.v)
    h.p.v = 99
    print(n.v)


main()

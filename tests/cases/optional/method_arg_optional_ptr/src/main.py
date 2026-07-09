# A record method taking a pointer-repr Optional[record] param, exercising the
# non-ctor arg faces: &(name), None, bare-pass param, optional_to_ptr field lift
# (@nocopy Node forbids a silent copy).
from tpy import Int32, Own, nocopy


@nocopy
class Node:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v


class Graph:
    total: Int32
    slot: Node | None

    def __init__(self) -> None:
        self.total = 0
        self.slot = None

    def link(self, n: Node | None) -> None:
        if n is not None:
            self.total += n.v

    def stash(self, n: Own[Node]) -> None:
        self.slot = n


def relay(g: Graph, n: Node | None) -> None:
    g.link(n)


def main() -> None:
    g = Graph()
    a = Node(5)
    g.link(a)
    g.link(None)
    relay(g, a)
    g.stash(Node(9))
    g.link(g.slot)
    print(g.total)


main()

# A pointer-Optional walrus whose SOURCE is a ternary: both arms already render
# as the `T*` the target holds, so the select lands bare -- in a plain function
# and in a generator, where the target is a frame field instead of a local.
from typing import Iterator
from tpy import int32


class Node:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Holder:
    opt: Node | None

    def __init__(self, v: int32) -> None:
        self.opt = Node(v)


def value_of(n: Node | None) -> int32:
    if n is not None:
        return n.v
    return -9


def pick(c: bool) -> int32:
    h = Holder(1)
    if (t := (h.opt if c else None)) is not None:  # tpyc: ok
        t.v += 3                    # writes through the alias
        inner = h.opt
        if inner is not None:
            return inner.v          # 4 -- the field saw it
    return -1


def walk(nodes: list[Node], flag: bool) -> Iterator[int32]:
    yield -1
    i = 0
    while i < 2:
        yield value_of(m := (nodes[i] if flag else None))  # tpyc: ok
        if m is not None:
            m.v += 100              # the frame field aliases the element
            print(m.v)
        i += 1


def main() -> None:
    print(pick(True), pick(False))
    nodes = [Node(1), Node(2)]
    for a in walk(nodes, True):
        print(a)
    print(nodes[0].v, nodes[1].v)


main()

from typing import Iterator
from tpy import Int32
class Node:
    v: Int32
    def __init__(self, v: Int32) -> None:
        self.v = v
def value_of(n: Node | None) -> Int32:
    if n is not None:
        return n.v
    return -9
def g(nodes: list[Node], flag: bool) -> Iterator[Int32]:
    yield -1
    i = 0
    while i < 2:
        yield value_of(m := (nodes[i] if flag else None))
        if m is not None:
            m.v += 100
            print(m.v)
        i += 1
def main() -> None:
    for a in g([Node(1), Node(2)], True):
        print(a)
main()

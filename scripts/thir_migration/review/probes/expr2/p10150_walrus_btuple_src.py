from typing import Iterator
from tpy import Int32
class Node:
    v: Int32
    def __init__(self, v: Int32) -> None:
        self.v = v
def g(nodes: list[Node]) -> Iterator[Int32]:
    yield -1
    i = 0
    while i < len(nodes):
        yield (bt := (i, nodes[i]))[0]
        bt[1].v += 1000
        print(bt[0], bt[1].v)
        i += 1
def main() -> None:
    for a in g([Node(1)]):
        print(a)
main()

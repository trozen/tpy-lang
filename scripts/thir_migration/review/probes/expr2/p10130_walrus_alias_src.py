from typing import Iterator
from tpy import int32
class Node:
    v: int32
    kid: list[int32]
    def __init__(self, v: int32) -> None:
        self.v = v
        self.kid = [v]
def g(nodes: list[Node]) -> Iterator[int32]:
    yield -1
    i = 0
    while i < len(nodes):
        yield len(row := nodes[i].kid)
        row.append(99)
        print(len(row))
        i += 1
def main() -> None:
    for a in g([Node(1)]):
        print(a)
main()

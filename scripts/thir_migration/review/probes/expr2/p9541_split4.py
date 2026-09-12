from tpy import int32, Own
from typing import Iterator
class Node:
    v: int32
    def __init__(self, v: int32) -> None:
        self.v = v
class Src:
    def __init__(self) -> None:
        pass
    def gen(self, xs: list[int32]) -> Iterator[int32]:
        for x in xs:
            yield x
    def gen2(self, n: Node) -> Iterator[int32]:
        yield n.v
        yield n.v
def mk() -> Own[Node]:
    return Node(1)
def total(it: Iterator[int32]) -> int32:
    t = 0
    for x in it:
        t += x
    return t
def a4() -> None:
    s = Src()
    print(total(s.gen2(mk())) + total(s.gen2(Node(1))))
a4()

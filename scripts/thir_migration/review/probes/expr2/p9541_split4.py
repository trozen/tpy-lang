from tpy import Int32, Own
from typing import Iterator
class Node:
    v: Int32
    def __init__(self, v: Int32) -> None:
        self.v = v
class Src:
    def __init__(self) -> None:
        pass
    def gen(self, xs: list[Int32]) -> Iterator[Int32]:
        for x in xs:
            yield x
    def gen2(self, n: Node) -> Iterator[Int32]:
        yield n.v
        yield n.v
def mk() -> Own[Node]:
    return Node(1)
def total(it: Iterator[Int32]) -> Int32:
    t = 0
    for x in it:
        t += x
    return t
def a4() -> None:
    s = Src()
    print(total(s.gen2(mk())) + total(s.gen2(Node(1))))
a4()

from tpy import int32, StrView, char, BytesView
from tplib import Box
class Node:
    v: int32
    def __init__(self, v: int32) -> None:
        self.v = v
def a1(o: StrView | None) -> str | None:
    return o
def a2(c: char) -> None:
    s: str = c
    print(s)
def a3() -> None:
    ba = bytearray(2)
    bv: BytesView = ba
    print(len(bv))
def a4() -> None:
    b = Box(Node(1))
    n: Node = b
    print(n.v)
def a5(c: char) -> str:
    return c
a1("x"); a2(char("y")); a3(); a4(); print(a5(char("z")))

from tpy import Int32, StrView, Char, BytesView
from tplib import Box
class Node:
    v: Int32
    def __init__(self, v: Int32) -> None:
        self.v = v
def a1(o: StrView | None) -> str | None:
    return o
def a2(c: Char) -> None:
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
def a5(c: Char) -> str:
    return c
a1("x"); a2(Char("y")); a3(); a4(); print(a5(Char("z")))

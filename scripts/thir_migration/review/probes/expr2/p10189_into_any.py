from typing import Any
from tpy import Int32
class Node:
    v: Int32
    def __init__(self, v: Int32) -> None:
        self.v = v
def a1() -> None:
    xs = [1, 2]
    a: Any = xs
    print(a is None)
def a2() -> None:
    a: Any = {"k": 1}
    print(a is None)
def a3() -> None:
    a: Any = [Node(1)]
    print(a is None)
def a4() -> None:
    n = Node(1)
    a: Any = [n]
    print(a is None)
def a5() -> None:
    a: Any = [1, 2]
    xs: list[Int32] = a
    print(len(xs))
a1(); a2(); a3(); a4(); a5()

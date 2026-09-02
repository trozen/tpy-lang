from tpy import Int32
class Node:
    v: Int32
    def __init__(self, v: Int32) -> None:
        self.v = v
def a1() -> None:
    xs: list[tuple[Int32, Int32] | None] = [(1, 2), None]
    print(len(xs))
def a2() -> None:
    n = Node(1)
    xs: list[tuple[Node, Int32] | None] = [(n, 2)]
    print(len(xs))
a1(); a2()

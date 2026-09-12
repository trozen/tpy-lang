from tpy import int32
class Node:
    v: int32
    def __init__(self, v: int32) -> None:
        self.v = v
def a1() -> None:
    xs: list[tuple[int32, int32] | None] = [(1, 2), None]
    print(len(xs))
def a2() -> None:
    n = Node(1)
    xs: list[tuple[Node, int32] | None] = [(n, 2)]
    print(len(xs))
a1(); a2()

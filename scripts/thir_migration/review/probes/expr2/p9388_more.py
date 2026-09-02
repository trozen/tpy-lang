from tpy import Int32, Own
class Node:
    v: Int32
    def __init__(self, v: Int32) -> None:
        self.v = v
    def clone(self) -> Own[Node]:
        return Node(self.v)
    def opt(self) -> Node | None:
        return self
class Tree:
    root: Node
    def __init__(self) -> None:
        self.root = Node(1)
    def pair(self) -> tuple[Node, Int32]:
        return (self.root, 1)
    def both(self) -> tuple[Int32, Int32]:
        return (1, 2)
def b1() -> None:
    n = Node(1)
    c = n.clone()
    print(c.v)
def b2() -> None:
    n = Node(1)
    print(n.clone().v)
def b3() -> None:
    n = Node(1)
    k = n.opt()
    if k is not None:
        print(k.v)
def b4() -> None:
    t = Tree()
    a, b = t.both()
    print(a + b)
def b5() -> None:
    t = Tree()
    print(t.pair()[1])
def b7() -> None:
    n = Node(1)
    m = n
    if n.opt() is not None:
        print(m.v)
b1(); b2(); b3(); b4(); b5(); b7()

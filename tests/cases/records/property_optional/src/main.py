# Property returning Optional[non-value-type] with getter and setter
from typing import Optional
from tpy import Int32

class Node:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v

class Wrapper:
    _node: Optional[Node]

    def __init__(self) -> None:
        self._node = None

    @property
    def node(self) -> Optional[Node]:
        return self._node

    @node.setter
    def node(self, n: Optional[Node]) -> None:
        self._node = n

def main() -> None:
    w = Wrapper()
    print(w.node is None)
    w.node = Node(42)
    n = w.node
    if n is not None:
        print(n.val)
    w.node = None
    print(w.node is None)

main()

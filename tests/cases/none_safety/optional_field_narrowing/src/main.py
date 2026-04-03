# Optional[non-value-type] field narrowing: dereference std::optional after is-not-None check
from typing import Optional
from tpy import Int32

class Node:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v
    def doubled(self) -> Int32:
        return self.val * 2

class Wrapper:
    _node: Optional[Node]

    def __init__(self) -> None:
        self._node = None

def test_field_narrowing() -> None:
    w = Wrapper()
    w._node = Node(42)
    if w._node is not None:
        print(w._node.val)
    else:
        print("none")

def test_nested_field() -> None:
    w = Wrapper()
    w._node = Node(99)
    assert w._node is not None
    print(w._node.val)

def test_alias() -> None:
    w = Wrapper()
    w._node = Node(7)
    if w._node is not None:
        n = w._node
        print(n.val)

def test_method_call() -> None:
    w = Wrapper()
    w._node = Node(6)
    if w._node is not None:
        print(w._node.doubled())

test_field_narrowing()
test_nested_field()
test_alias()
test_method_call()

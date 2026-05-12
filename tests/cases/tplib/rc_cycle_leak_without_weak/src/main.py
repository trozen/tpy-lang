# All-strong Rc[T] cycles leak. Pair test with `weak_cycle_breaks`: that
# case uses a Weak back-edge and drops both nodes; this one wires both
# edges as strong and demonstrates __del__ never fires for either node.
# Documents that Weak is the recommended fix, not an optional polish.
from __future__ import annotations
from tpy import Int32
from tplib import Rc


class Node:
    name: str
    next: Rc[Node] | None

    def __init__(self, name: str) -> None:
        self.name = name
        self.next = None
        print("init", name)

    def __del__(self) -> None:
        print("del", self.name)


def acyclic() -> None:
    # a -> b, no back-edge. Both drop when a goes out of scope.
    a = Rc.new(Node("A"))
    b = Rc.new(Node("B"))
    a.get().next = b.clone()
    # Dropping `b` first (still held by a.next via the clone) keeps B alive
    # until A drops at scope end.


def cyclic() -> None:
    # a -> b -> a. Refcount on each cell stays >= 1 after handles go out
    # of scope; __del__ never fires for either node.
    a = Rc.new(Node("X"))
    b = Rc.new(Node("Y"))
    a.get().next = b.clone()
    b.get().next = a.clone()


def main() -> None:
    print("--- acyclic ---")
    acyclic()
    print("--- cyclic (will leak) ---")
    cyclic()
    print("--- done ---")


main()

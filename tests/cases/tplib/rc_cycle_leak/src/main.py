# Rc[T] cycles leak. Without a `Weak[T]` companion, a -> b -> a keeps
# both allocations alive after the user's handles go out of scope, so
# __del__ never fires. This test documents the limitation: a non-cyclic
# pair drops both nodes (2 destructor calls); the cyclic pair drops
# zero. When Weak[T] eventually ships, this test should be revised to
# show that a `Weak`-broken cycle drops correctly.
from __future__ import annotations
from tpy import Int32
from tplib import Rc, make_rc


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
    a = make_rc(Node("A"))
    b = make_rc(Node("B"))
    a.get().next = b.clone()
    # Dropping `b` first (still held by a.next via the clone) keeps B alive
    # until A drops at scope end.


def cyclic() -> None:
    # a -> b -> a. Refcount on each cell stays >= 1 after handles go out
    # of scope; __del__ never fires for either node.
    a = make_rc(Node("X"))
    b = make_rc(Node("Y"))
    a.get().next = b.clone()
    b.get().next = a.clone()


def main() -> None:
    print("--- acyclic ---")
    acyclic()
    print("--- cyclic (will leak) ---")
    cyclic()
    print("--- done ---")


main()

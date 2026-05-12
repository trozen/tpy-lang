# Rc[T] breaks type-recursion cycles. Without Rc registered as
# indirecting in tpyc/cycle_detection.py, this would fail to compile
# (Node would have infinite size in C++).
from __future__ import annotations
from tpy import Int32
from tplib import Rc, make_rc


class Node:
    value: Int32
    next: Rc[Node] | None

    def __init__(self, value: Int32) -> None:
        self.value = value
        self.next = None


def main() -> None:
    a = make_rc(Node(Int32(1)))
    b = make_rc(Node(Int32(2)))
    c = make_rc(Node(Int32(3)))

    a.get().next = b.clone()
    b.get().next = c.clone()

    # Walk the chain.
    cur = a.clone()
    while True:
        print(cur.get().value)
        nxt = cur.get().next
        if nxt is None:
            break
        cur = nxt.clone()


main()

# Rc[T] breaks type-recursion cycles. Without Rc registered as
# indirecting in tpyc/cycle_detection.py, this would fail to compile
# (Node would have infinite size in C++).
from __future__ import annotations
from tpy import int32
from tplib import Rc


class Node:
    value: int32
    next: Rc[Node] | None

    def __init__(self, value: int32) -> None:
        self.value = value
        self.next = None


def main() -> None:
    a = Rc.new(Node(int32(1)))
    b = Rc.new(Node(int32(2)))
    c = Rc.new(Node(int32(3)))

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

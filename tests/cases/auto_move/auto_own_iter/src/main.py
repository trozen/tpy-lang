# iter() works on Own[list[T]] inside consuming methods.
# Verifies that overload resolution unwraps Own for protocol matching.
from __future__ import annotations
from typing import Self
from tpy import Int32, Own, auto_own

class Stack:
    items: list[Int32]
    def __init__(self) -> None:
        self.items = [1, 2, 3]

    def consume(self: auto_own[Self]) -> auto_own[Int32]:
        it = iter(self.items)
        total: Int32 = 0
        for x in it:
            total += x
        return total

def main() -> None:
    s = Stack()
    # Both calls use borrowing (consuming dispatch is for-loop only)
    print(s.consume())
    print(s.consume())

main()

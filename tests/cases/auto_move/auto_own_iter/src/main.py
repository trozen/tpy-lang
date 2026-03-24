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
    # Borrowing clone
    print(s.consume())
    # Consuming clone (s at last use)
    print(s.consume())

main()

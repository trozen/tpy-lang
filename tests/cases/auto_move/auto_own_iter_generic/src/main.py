# Generic user-defined __iter__ with auto_own[Self].
# Verifies that the consuming clone emits valid C++ for a generic class (auto return type).
from __future__ import annotations
from typing import Self, Iterator
from tpy import Int32, auto_own

class Stack[T]:
    items: list[T]
    def __init__(self, vals: list[T]) -> None:
        self.items = vals

    def __iter__(self: auto_own[Self]) -> auto_own[Iterator[T]]:
        return iter(self.items)

def main() -> None:
    s = Stack[Int32]([10, 20, 30])
    # Borrowing iteration
    total: Int32 = 0
    for x in s:
        total += x
    print(total)
    # Consuming iteration (s at last use)
    for x in s:
        print(x)

main()

# User-defined __iter__ with auto_own[Self] returning Iterator protocol.
# Verifies that consuming clone gets `auto` C++ return type (not unresolved `T`).
from __future__ import annotations
from typing import Self, Iterator
from tpy import Int32, auto_own

class IntList:
    items: list[Int32]
    def __init__(self, vals: list[Int32]) -> None:
        self.items = vals

    def __iter__(self: auto_own[Self]) -> auto_own[Iterator[Int32]]:
        return iter(self.items)

def main() -> None:
    xs = IntList([10, 20, 30])
    # Borrowing iteration (xs still alive after)
    total: Int32 = 0
    for x in xs:
        total += x
    print(total)
    # Consuming iteration (xs at last use)
    for x in xs:
        print(x)

main()

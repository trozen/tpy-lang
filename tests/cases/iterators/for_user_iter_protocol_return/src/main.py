# For-loop over user type whose __iter__ returns Iterator[T] (protocol type).
# Verifies that protocol return types from __iter__ are recognized as iterable.
from __future__ import annotations
from typing import Iterator
from tpy import int32

class Stack:
    items: list[int32]
    def __init__(self) -> None:
        self.items = [1, 2, 3]

    def __iter__(self) -> Iterator[int32]:
        return iter(self.items)

def main() -> None:
    s = Stack()
    total: int32 = 0
    for x in s:
        total += x
    print(total)

main()

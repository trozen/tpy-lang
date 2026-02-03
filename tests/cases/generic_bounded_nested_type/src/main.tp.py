# Test generic protocol with nested type parameter (e.g., list[T])
from __future__ import annotations
from typing import Protocol
from tpy import Int32

class ItemsProvider[T](Protocol):
    def items(self) -> list[T]: ...

class IntListHolder:
    data: list[Int32]

    def __init__(self, data: list[Int32]) -> None:
        self.data = data

    def items(self) -> list[Int32]:
        return self.data

# Bound is ItemsProvider[Int32], so items() must return list[Int32]
class Wrapper[V: ItemsProvider[Int32]]:
    holder: V

    def __init__(self, holder: V) -> None:
        self.holder = holder

    def get_holder(self) -> V:
        return self.holder

def main() -> None:
    h = IntListHolder([1, 2, 3])
    w = Wrapper[IntListHolder](h)
    result = w.get_holder()
    print(len(result.items()))

main()

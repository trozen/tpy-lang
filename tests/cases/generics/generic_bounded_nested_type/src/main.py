# Test generic protocol with nested type parameter (e.g., list[T])
from __future__ import annotations
from typing import Protocol
from tpy import int32

class ItemsProvider[T](Protocol):
    def items(self) -> list[T]: ...

class IntListHolder:
    data: list[int32]

    def __init__(self, data: list[int32]) -> None:
        self.data = data

    def items(self) -> list[int32]:
        return self.data

# Bound is ItemsProvider[int32], so items() must return list[int32]
class Wrapper[V: ItemsProvider[int32]]:
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

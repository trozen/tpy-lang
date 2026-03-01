# Static protocol types in method and constructor parameters
from typing import Protocol
from tpy import readonly, copy

class HasLength(Protocol):
    @readonly
    def length(self) -> int:
        ...

class Words:
    items: list[str]
    def __init__(self, items: list[str]) -> None:
        self.items = copy(items)
    @readonly
    def length(self) -> int:
        return len(self.items)

class Numbers:
    items: list[int]
    def __init__(self, items: list[int]) -> None:
        self.items = copy(items)
    @readonly
    def length(self) -> int:
        return len(self.items)

class Container:
    count: int

    def __init__(self, items: HasLength) -> None:
        self.count = items.length()

    def update(self, items: HasLength) -> None:
        self.count = items.length()

    @readonly
    def combined_len(self, other: HasLength) -> int:
        return self.count + other.length()

def main() -> None:
    w = Words(["hello", "world", "!"])
    c = Container(w)
    print(c.count)
    n = Numbers([10, 20])
    c.update(n)
    print(c.count)
    print(c.combined_len(w))

main()

# Test: isinstance check against a protocol that is not a member of the union
from typing import Protocol


class Measurable(Protocol):
    def measure(self) -> int: ...

class Walkable(Protocol):
    def walk(self) -> int: ...

class Printable(Protocol):
    def display(self) -> str: ...


def check(items: Measurable | Walkable) -> int:
    if isinstance(items, Printable):  # tpyc: error(/not a member/)
        return 0
    return 1

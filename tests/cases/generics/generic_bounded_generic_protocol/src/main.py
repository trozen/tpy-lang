# Test bounded type parameter with generic user-defined protocol
from __future__ import annotations
from typing import Protocol
from tpy import int32

class Container[T](Protocol):
    def get(self) -> T: ...

class IntBox:
    value: int32

    def __init__(self, v: int32) -> None:
        self.value = v

    def get(self) -> int32:
        return self.value

# Bound is a parameterized user-defined protocol: Container[int32]
class Holder[V: Container[int32]]:
    item: V

    def __init__(self, item: V) -> None:
        self.item = item

    def get_item(self) -> V:
        return self.item

def main() -> None:
    box = IntBox(42)
    # IntBox satisfies Container[int32]
    h = Holder[IntBox](box)
    # Call get() on the concrete type after retrieval
    result = h.get_item()
    print(result.get())

main()

# Test bounded type parameters with user-defined protocol
from __future__ import annotations
from typing import Protocol
from tpy import Int32

class Addable(Protocol):
    def add(self, x: Int32) -> Int32: ...

class MyNumber:
    value: Int32

    def __init__(self, v: Int32) -> None:
        self.value = v

    def add(self, x: Int32) -> Int32:
        return self.value + x

class Holder[T: Addable]:
    item: T

    def __init__(self, item: T):
        self.item = item

    def get_item(self) -> T:
        return self.item

def main() -> None:
    # MyNumber satisfies Addable protocol
    h = Holder[MyNumber](MyNumber(10))
    num = h.get_item()
    print(num.add(5))  # Should print 15

main()

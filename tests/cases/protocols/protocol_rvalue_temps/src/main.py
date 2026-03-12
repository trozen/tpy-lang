"""Test rvalue expressions passed to protocol-typed parameters.

Protocol params use T& in generated C++, which can't bind rvalues directly.
The compiler generates temp variables for these cases.
"""
from __future__ import annotations
from typing import Protocol
from tpy import Int32, readonly_alt

# Protocol that accepts various types
class HasValue(Protocol):
    def get(self) -> Int32: ...

class IntBox:
    v: Int32
    def __init__(self, v: Int32) -> None:
        self.v = v
    def get(self) -> Int32:
        return self.v

# Container of boxes for subscript test
class BoxContainer:
    items: list[IntBox]
    def __init__(self) -> None:
        self.items = [IntBox(10), IntBox(20), IntBox(30)]
    @readonly_alt
    def __getitem__(self, i: Int32) -> IntBox:
        return self.items[i]

def show(h: HasValue) -> None:
    print(h.get())

def main() -> None:
    # Test 1: Constructor call (rvalue)
    show(IntBox(42))

    # Test 2: Function returning value (rvalue)
    # Note: Can't easily test this without Own[] which has its own restrictions
    # Tested via constructor which is similar

    # Test 3: Builtin function result
    # len() returns Int32, not a HasValue, so can't test directly
    # But we test builtin in method args via protocol_method_coercion

    # Test 4: Record subscript (rvalue - __getitem__ returns by value)
    container = BoxContainer()
    show(container[1])  # Should create temp for subscript result

    # Test 5: Multiple temps in one call sequence
    show(IntBox(100))
    show(container[0])
    show(container[2])

main()

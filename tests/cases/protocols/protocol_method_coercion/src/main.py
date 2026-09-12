from __future__ import annotations
from typing import Protocol
from tpy import int32

class Calculator(Protocol):
    def add(self, x: int32) -> int32: ...
    def multiply(self, x: int32, y: int32) -> int32: ...

class SimpleCalc:
    base: int32

    def __init__(self, b: int32) -> None:
        self.base = b

    def add(self, x: int32) -> int32:
        return self.base + x

    def multiply(self, x: int32, y: int32) -> int32:
        return x * y

def use_calc(c: Calculator) -> None:
    # Test: Literal coercion to int32 in protocol method calls
    result1 = c.add(10)
    print(result1)

    result2 = c.multiply(6, 7)
    print(result2)

def main() -> None:
    calc = SimpleCalc(32)
    use_calc(calc)

    # Also test passing constructor as rvalue
    use_calc(SimpleCalc(0))

main()

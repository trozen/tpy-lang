from __future__ import annotations
from typing import Protocol, Self
from tpy import Int32, Own

class Duplicable(Protocol):
    def duplicate(self) -> Own[Self]: ...

class Value:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x

    def duplicate(self) -> Own[Value]:
        return Value(self.x * 2)

def double_it(d: Duplicable) -> None:
    result = d.duplicate()

def main() -> None:
    v = Value(21)
    double_it(v)

    # Direct call to verify it works
    v2 = v.duplicate()
    print(v2.x)

main()

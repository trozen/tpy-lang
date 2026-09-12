# User-defined protocol with same name as builtin tpy.Comparable.
# Verifies builtin protocol checks still work when the name is shadowed.
from typing import Protocol
from tpy import int32
from enum import IntEnum

class Comparable(Protocol):
    def compare_to(self) -> int32: ...

class Priority(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3

class Widget(Comparable):
    val: int32
    def __init__(self, val: int32):
        self.val = val
    def compare_to(self) -> int32:
        return self.val

def use_user_comparable(x: Comparable) -> int32:
    return x.compare_to()

def main() -> None:
    # User Comparable protocol works (explicit implementation)
    w = Widget(42)
    print(use_user_comparable(w))
    # Builtin Comparable still works (IntEnum comparison)
    print(Priority.LOW < Priority.HIGH)
    print(Priority.HIGH < Priority.LOW)

main()

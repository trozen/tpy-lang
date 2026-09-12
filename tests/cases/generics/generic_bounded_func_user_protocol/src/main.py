# Test bounded generic function with user-defined protocol
from __future__ import annotations
from typing import Protocol
from tpy import int32

class Printable(Protocol):
    def to_string(self) -> str: ...

class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

    def to_string(self) -> str:
        return "Point"

def print_item[T: Printable](item: T) -> None:
    # Note: Can't call item.to_string() inside generic yet
    # This tests that the bound is validated during inference
    print("got printable")

def main() -> None:
    p = Point(10, 20)
    # Point satisfies Printable, so inference should work
    print_item(p)

main()

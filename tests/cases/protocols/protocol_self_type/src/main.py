from typing import Protocol, Self
from tpy import int32

class Addable(Protocol):
    def __add__(self, other: Self) -> Self: ...

def add_values(x: Addable, y: Addable) -> None:
    # Just verifies that x + y is valid for Addable types
    result = x + y
    print(result)

def main() -> None:
    a: int32 = 21
    b: int32 = 21
    add_values(a, b)

main()

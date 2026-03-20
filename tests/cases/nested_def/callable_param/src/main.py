# Test passing nested def to Callable parameter
from typing import Callable
from tpy import Int32

def invoke(f: Callable[[Int32], Int32], x: Int32) -> Int32:
    return f(x)

def main() -> None:
    offset: Int32 = 50
    def add_offset(x: Int32) -> Int32:
        return x + offset
    print(invoke(add_offset, 7))

main()

# Test passing nested def to Callable parameter
from typing import Callable
from tpy import int32

def invoke(f: Callable[[int32], int32], x: int32) -> int32:
    return f(x)

def main() -> None:
    offset: int32 = 50
    def add_offset(x: int32) -> int32:
        return x + offset
    print(invoke(add_offset, 7))

main()

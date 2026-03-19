# Test error: calling Callable with wrong number of arguments
from typing import Callable
from tpy import Int32

def apply(f: Callable[[Int32], Int32], x: Int32) -> Int32:
    return f(x)

def main() -> None:
    f: Callable[[Int32], Int32] = lambda x: x
    f(1, 2)  # tpyc: error(/expects 1 argument/)

main()

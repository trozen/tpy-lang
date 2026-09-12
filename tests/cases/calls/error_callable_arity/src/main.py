# Test error: calling Callable with wrong number of arguments
from typing import Callable
from tpy import int32

def apply(f: Callable[[int32], int32], x: int32) -> int32:
    return f(x)

def main() -> None:
    f: Callable[[int32], int32] = lambda x: x
    f(1, 2)  # tpyc: error(/expects 1 argument/)

main()

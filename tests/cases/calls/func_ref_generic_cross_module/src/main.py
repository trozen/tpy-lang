# Test cross-module generic function references with qualified C++ names
from typing import Callable
from tpy import Fn, int32
from helper import identity, swap

def apply(f: Fn[[int32], int32], x: int32) -> int32:
    return f(x)

def apply_swap(f: Fn[[int32, int32], tuple[int32, int32]], a: int32, b: int32) -> tuple[int32, int32]:
    return f(a, b)

def main() -> None:
    # Cross-module generic ref: identity[T] from helper
    print(apply(identity, 42))               # 42

    # Cross-module multi-param generic ref: swap[T, U] from helper
    print(apply_swap(swap, 1, 2))            # (2, 1)

    # Cross-module generic ref as Callable local
    f: Callable[[int32], int32] = identity
    print(f(7))                               # 7

main()

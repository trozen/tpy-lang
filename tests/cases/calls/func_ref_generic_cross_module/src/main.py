# Test cross-module generic function references with qualified C++ names
from typing import Callable
from tpy import Fn, Int32
from helper import identity, swap

def apply(f: Fn[[Int32], Int32], x: Int32) -> Int32:
    return f(x)

def apply_swap(f: Fn[[Int32, Int32], tuple[Int32, Int32]], a: Int32, b: Int32) -> tuple[Int32, Int32]:
    return f(a, b)

def main() -> None:
    # Cross-module generic ref: identity[T] from helper
    print(apply(identity, 42))               # 42

    # Cross-module multi-param generic ref: swap[T, U] from helper
    print(apply_swap(swap, 1, 2))            # (2, 1)

    # Cross-module generic ref as Callable local
    f: Callable[[Int32], Int32] = identity
    print(f(7))                               # 7

main()

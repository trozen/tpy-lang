# Test Callable as function parameter (type-erased std::function)
from typing import Callable
from tpy import int32

def apply(f: Callable[[int32], int32], x: int32) -> int32:
    return f(x)

def apply_void(f: Callable[[int32], None], x: int32) -> None:
    f(x)

def apply_multi(f: Callable[[int32, int32], int32], a: int32, b: int32) -> int32:
    return f(a, b)

def apply_zero(f: Callable[[], int32]) -> int32:
    return f()

def main() -> None:
    print(apply(lambda x: x + 1, 10))
    print(apply(lambda x: x * 3, 7))
    apply_void(lambda x: print(x), 99)
    print(apply_multi(lambda a, b: a + b, 3, 4))
    print(apply_zero(lambda: 42))

main()

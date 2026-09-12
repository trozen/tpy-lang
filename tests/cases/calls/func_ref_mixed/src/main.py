# Test function references with mixed Fn/Callable and various signatures
from typing import Callable
from tpy import Fn, int32

def to_str(x: int32) -> str:
    return str(x)

def double(x: int32) -> int32:
    return x * 2

def apply_fn(f: Fn[[int32], int32], x: int32) -> int32:
    return f(x)

def apply_callable(f: Callable[[int32], int32], x: int32) -> int32:
    return f(x)

def transform(f: Fn[[int32], str], x: int32) -> str:
    return f(x)

# Return Callable wrapping a named function
def get_doubler() -> Callable[[int32], int32]:
    return double

def main() -> None:
    # Same function used with both Fn and Callable
    print(apply_fn(double, 21))       # 42
    print(apply_callable(double, 21)) # 42

    # Different signature function
    print(transform(to_str, 99))      # 99

    # Return as Callable
    f = get_doubler()
    print(f(10))  # 20

main()

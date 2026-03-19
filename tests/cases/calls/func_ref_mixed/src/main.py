# Test function references with mixed Fn/Callable and various signatures
from typing import Callable
from tpy import Fn, Int32

def to_str(x: Int32) -> str:
    return str(x)

def double(x: Int32) -> Int32:
    return x * 2

def apply_fn(f: Fn[[Int32], Int32], x: Int32) -> Int32:
    return f(x)

def apply_callable(f: Callable[[Int32], Int32], x: Int32) -> Int32:
    return f(x)

def transform(f: Fn[[Int32], str], x: Int32) -> str:
    return f(x)

# Return Callable wrapping a named function
def get_doubler() -> Callable[[Int32], Int32]:
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

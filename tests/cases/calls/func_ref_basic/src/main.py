# Test named function references passed to Fn parameters
from tpy import Fn, int32

def double(x: int32) -> int32:
    return x * 2

def negate(x: int32) -> int32:
    return -x

def add(a: int32, b: int32) -> int32:
    return a + b

def apply(f: Fn[[int32], int32], x: int32) -> int32:
    return f(x)

def apply2(f: Fn[[int32, int32], int32], a: int32, b: int32) -> int32:
    return f(a, b)

def run_void(f: Fn[[int32], None], x: int32) -> None:
    f(x)

def print_val(x: int32) -> None:
    print(x)

def main() -> None:
    print(apply(double, 21))   # 42
    print(apply(negate, 5))    # -5
    print(apply2(add, 3, 4))   # 7
    run_void(print_val, 99)    # 99
    # Non-void function passed to void hint (return value discarded)
    run_void(double, 7)        # (no output -- double returns int32, discarded)

main()

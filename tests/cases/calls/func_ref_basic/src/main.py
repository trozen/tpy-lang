# Test named function references passed to Fn parameters
from tpy import Fn, Int32

def double(x: Int32) -> Int32:
    return x * 2

def negate(x: Int32) -> Int32:
    return -x

def add(a: Int32, b: Int32) -> Int32:
    return a + b

def apply(f: Fn[[Int32], Int32], x: Int32) -> Int32:
    return f(x)

def apply2(f: Fn[[Int32, Int32], Int32], a: Int32, b: Int32) -> Int32:
    return f(a, b)

def run_void(f: Fn[[Int32], None], x: Int32) -> None:
    f(x)

def print_val(x: Int32) -> None:
    print(x)

def main() -> None:
    print(apply(double, 21))   # 42
    print(apply(negate, 5))    # -5
    print(apply2(add, 3, 4))   # 7
    run_void(print_val, 99)    # 99
    # Non-void function passed to void hint (return value discarded)
    run_void(double, 7)        # (no output -- double returns Int32, discarded)

main()

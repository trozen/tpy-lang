# Function with multiple Fn parameters (each gets its own template param).
from tpy import Fn, int32

def apply_both(f: Fn[[int32], int32], g: Fn[[int32], int32], x: int32) -> int32:
    return g(f(x))

def do_both(a: Fn[[int32], None], b: Fn[[int32], None], x: int32) -> None:
    a(x)
    b(x)

def main() -> None:
    print(apply_both(lambda x: x + 1, lambda x: x * 2, 5))
    print(apply_both(lambda x: x * 3, lambda x: x - 1, 4))
    do_both(lambda x: print(x), lambda x: print(x + 100), 7)

main()

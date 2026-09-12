# Test passing nested def to Fn parameter (zero-cost)
from tpy import Fn, int32

def apply(f: Fn[[int32], int32], x: int32) -> int32:
    return f(x)

def main() -> None:
    y: int32 = 100
    def add_y(x: int32) -> int32:
        return x + y
    print(apply(add_y, 42))

main()

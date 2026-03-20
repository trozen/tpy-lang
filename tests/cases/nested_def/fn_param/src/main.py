# Test passing nested def to Fn parameter (zero-cost)
from tpy import Fn, Int32

def apply(f: Fn[[Int32], Int32], x: Int32) -> Int32:
    return f(x)

def main() -> None:
    y: Int32 = 100
    def add_y(x: Int32) -> Int32:
        return x + y
    print(apply(add_y, 42))

main()

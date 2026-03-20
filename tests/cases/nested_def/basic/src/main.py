# Test nested def with value type capture
from tpy import Int32

def main() -> None:
    x: Int32 = 10
    def add_x(n: Int32) -> Int32:
        return n + x
    print(add_x(5))
    print(add_x(32))

main()

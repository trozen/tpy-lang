# Test nested def with value type capture
from tpy import int32

def main() -> None:
    x: int32 = 10
    def add_x(n: int32) -> int32:
        return n + x
    print(add_x(5))
    print(add_x(32))

main()

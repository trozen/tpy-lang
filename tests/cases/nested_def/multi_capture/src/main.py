# Test nested def capturing multiple variables
from tpy import Int32

def main() -> None:
    a: Int32 = 10
    b: Int32 = 20
    c: Int32 = 30
    def sum_all(x: Int32) -> Int32:
        return x + a + b + c
    print(sum_all(0))
    print(sum_all(40))

main()

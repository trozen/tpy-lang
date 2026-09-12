# Test nested def capturing multiple variables
from tpy import int32

def main() -> None:
    a: int32 = 10
    b: int32 = 20
    c: int32 = 30
    def sum_all(x: int32) -> int32:
        return x + a + b + c
    print(sum_all(0))
    print(sum_all(40))

main()

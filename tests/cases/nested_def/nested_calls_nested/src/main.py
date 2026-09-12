# Test one nested def calling another
from tpy import int32

def main() -> None:
    def double(x: int32) -> int32:
        return x * 2
    def quadruple(x: int32) -> int32:
        return double(double(x))
    print(quadruple(3))

main()

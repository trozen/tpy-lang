# Test one nested def calling another
from tpy import Int32

def main() -> None:
    def double(x: Int32) -> Int32:
        return x * 2
    def quadruple(x: Int32) -> Int32:
        return double(double(x))
    print(quadruple(3))

main()

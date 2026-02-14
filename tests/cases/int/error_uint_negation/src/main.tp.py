# Test that negation on unsigned types is a compile error
from tpy import UInt8

def main() -> None:
    x: UInt8 = UInt8(42)
    y: UInt8 = -x  # tpyc: error(/unary.*UInt8/)
    print(y)

main()

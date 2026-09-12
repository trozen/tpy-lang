# Test that negation on unsigned types is a compile error
from tpy import uint8

def main() -> None:
    x: uint8 = uint8(42)
    y: uint8 = -x  # tpyc: error(/unary.*uint8/)
    print(y)

main()

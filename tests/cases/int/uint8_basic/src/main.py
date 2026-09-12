# Test uint8 basic operations
from tpy import uint8

def main() -> None:
    # Constructors
    a: uint8 = uint8(0)
    b: uint8 = uint8(255)
    c: uint8 = uint8(42)

    print(a)
    print(b)
    print(c)

    # Arithmetic
    x: uint8 = uint8(100)
    y: uint8 = uint8(50)
    print(x + y)
    print(x - y)
    print(x * uint8(2))
    print(x // uint8(3))
    print(x % uint8(7))

    # Bitwise
    print(uint8(0xFF) & uint8(0x0F))
    print(uint8(0xF0) | uint8(0x0F))
    print(uint8(0xFF) ^ uint8(0x0F))
    print(~uint8(0))

    # Conversion to BigInt
    n: int = int(c)
    print(n)

    # Conversion to str
    print(str(c))

main()

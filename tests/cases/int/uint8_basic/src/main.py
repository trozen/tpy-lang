# Test UInt8 basic operations
from tpy import UInt8

def main() -> None:
    # Constructors
    a: UInt8 = UInt8(0)
    b: UInt8 = UInt8(255)
    c: UInt8 = UInt8(42)

    print(a)
    print(b)
    print(c)

    # Arithmetic
    x: UInt8 = UInt8(100)
    y: UInt8 = UInt8(50)
    print(x + y)
    print(x - y)
    print(x * UInt8(2))
    print(x // UInt8(3))
    print(x % UInt8(7))

    # Bitwise
    print(UInt8(0xFF) & UInt8(0x0F))
    print(UInt8(0xF0) | UInt8(0x0F))
    print(UInt8(0xFF) ^ UInt8(0x0F))
    print(~UInt8(0))

    # Conversion to BigInt
    n: int = int(c)
    print(n)

    # Conversion to str
    print(str(c))

main()

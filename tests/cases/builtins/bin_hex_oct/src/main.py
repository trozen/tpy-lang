# bin(), hex(), oct() builtins
from tpy import Int64

def main() -> None:
    # bin
    print(bin(0))
    print(bin(1))
    print(bin(42))
    print(bin(255))
    print(bin(-1))
    print(bin(-42))

    # hex
    print(hex(0))
    print(hex(1))
    print(hex(255))
    print(hex(256))
    print(hex(-1))

    # oct
    print(oct(0))
    print(oct(1))
    print(oct(8))
    print(oct(64))
    print(oct(-8))

    # Int64
    big: Int64 = 1000000000000
    print(hex(big))

main()

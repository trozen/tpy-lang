# float (Float64) mixes with every fixed-width int width (incl. Int32), both
# operand orders, all 7 arithmetic ops. Whole-number results for CPython parity.
from tpy import Int8, Int16, Int32, Int64, UInt8, UInt16, UInt32, UInt64


def forward() -> None:
    print(10.0 + Int8(5))
    print(10.0 - Int16(3))
    print(4.0 * Int32(3))
    print(12.0 / Int64(4))
    print(13.0 // UInt8(5))
    print(13.0 % UInt16(5))
    print(2.0 ** UInt32(3))
    print(10.0 + UInt64(5))


def reverse() -> None:
    print(Int8(5) + 10.0)
    print(Int16(3) - 10.0)
    print(Int32(3) * 4.0)
    print(Int64(12) / 4.0)
    print(UInt8(13) // 5.0)
    print(UInt16(13) % 5.0)
    print(UInt32(2) ** 3.0)
    print(UInt64(5) + 10.0)


def main() -> None:
    forward()
    reverse()


main()

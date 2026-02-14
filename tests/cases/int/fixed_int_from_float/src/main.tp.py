# Test fixed-int construction from float at boundary values
from tpy import Int8, Int16, Int32, Int64, UInt8, UInt16, UInt32, UInt64

def main() -> None:
    # Int8 boundaries: -128 to 127
    print(Int8(-128.0))
    print(Int8(127.0))
    print(Int8(0.0))
    print(Int8(-1.9))  # truncates to -1
    print(Int8(1.9))   # truncates to 1

    # UInt8 boundaries: 0 to 255
    print(UInt8(0.0))
    print(UInt8(255.0))
    print(UInt8(1.7))  # truncates to 1

    # Int16 boundaries: -32768 to 32767
    print(Int16(-32768.0))
    print(Int16(32767.0))

    # UInt16 boundaries: 0 to 65535
    print(UInt16(0.0))
    print(UInt16(65535.0))

    # Int32 boundaries: -2147483648 to 2147483647
    print(Int32(-2147483648.0))
    print(Int32(2147483647.0))

    # UInt32 boundaries: 0 to 4294967295
    print(UInt32(0.0))
    print(UInt32(4294967295.0))

    # Int64: large values representable by double
    print(Int64(0.0))
    print(Int64(-1000000000000.0))
    print(Int64(1000000000000.0))

    # UInt64: large values representable by double
    print(UInt64(0.0))
    print(UInt64(1000000000000.0))

main()

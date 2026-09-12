# Test fixed-int construction from float at boundary values
from tpy import int8, int16, int32, int64, uint8, uint16, uint32, uint64

def main() -> None:
    # int8 boundaries: -128 to 127
    print(int8(-128.0))
    print(int8(127.0))
    print(int8(0.0))
    print(int8(-1.9))  # truncates to -1
    print(int8(1.9))   # truncates to 1

    # uint8 boundaries: 0 to 255
    print(uint8(0.0))
    print(uint8(255.0))
    print(uint8(1.7))  # truncates to 1

    # int16 boundaries: -32768 to 32767
    print(int16(-32768.0))
    print(int16(32767.0))

    # uint16 boundaries: 0 to 65535
    print(uint16(0.0))
    print(uint16(65535.0))

    # int32 boundaries: -2147483648 to 2147483647
    print(int32(-2147483648.0))
    print(int32(2147483647.0))

    # uint32 boundaries: 0 to 4294967295
    print(uint32(0.0))
    print(uint32(4294967295.0))

    # int64: large values representable by double
    print(int64(0.0))
    print(int64(-1000000000000.0))
    print(int64(1000000000000.0))

    # uint64: large values representable by double
    print(uint64(0.0))
    print(uint64(1000000000000.0))

main()

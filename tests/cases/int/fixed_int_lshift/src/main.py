# Test left-shift operations on various fixed-int types
from tpy import int8, int16, int32, int64, uint8, uint16

def main() -> None:
    # Basic shifts
    print(int8(1) << int8(0))    # 1
    print(int8(1) << int8(6))    # 64
    print(int8(-1) << int8(0))   # -1

    # uint8 shifts
    print(uint8(1) << uint8(7))  # 128
    print(uint8(3) << uint8(4))  # 48

    # int16 shifts
    print(int16(1) << int16(14))  # 16384

    # uint16 shifts
    print(uint16(1) << uint16(15))  # 32768

    # int32 shifts
    print(int32(1) << int32(30))  # 1073741824

    # int64 shifts
    print(int64(1) << int64(62))  # 4611686018427387904

main()

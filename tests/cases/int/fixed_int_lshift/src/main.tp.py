# Test left-shift operations on various fixed-int types
from tpy import Int8, Int16, Int32, Int64, UInt8, UInt16

def main() -> None:
    # Basic shifts
    print(Int8(1) << Int8(0))    # 1
    print(Int8(1) << Int8(6))    # 64
    print(Int8(-1) << Int8(0))   # -1

    # UInt8 shifts
    print(UInt8(1) << UInt8(7))  # 128
    print(UInt8(3) << UInt8(4))  # 48

    # Int16 shifts
    print(Int16(1) << Int16(14))  # 16384

    # UInt16 shifts
    print(UInt16(1) << UInt16(15))  # 32768

    # Int32 shifts
    print(Int32(1) << Int32(30))  # 1073741824

    # Int64 shifts
    print(Int64(1) << Int64(62))  # 4611686018427387904

main()

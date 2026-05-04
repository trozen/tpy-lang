# Wrapping shifts: Int{8,16,32,64}.{shl,shr}_wrap and the UInt versions.
# shl_wrap silently drops bits shifted out the top (no overflow panic).
# shr_wrap on signed types is logical (zero-fill via unsigned route),
# distinct from `>>` which is arithmetic / sign-extending.
from tpy import Int8, Int16, Int32, Int64, UInt8, UInt16, UInt32, UInt64

def main() -> None:
    # shl_wrap signed: high bits drop silently
    print(Int8.shl_wrap(Int8(64), Int8(2)))
    print(Int8.shl_wrap(Int8(1), Int8(7)))
    print(Int8.shl_wrap(Int8(-2), Int8(7)))      # 0xFE << 7 -> 0
    print(Int16.shl_wrap(Int16(0x4000), Int16(2)))
    print(Int32.shl_wrap(Int32(0x40000000), Int32(2)))
    print(Int64.shl_wrap(Int64(1), Int64(63)))

    # shl_wrap unsigned
    print(UInt8.shl_wrap(UInt8(0x80), UInt8(1)))
    print(UInt16.shl_wrap(UInt16(0x8000), UInt16(1)))
    print(UInt32.shl_wrap(UInt32(0x80000000), UInt32(1)))
    print(UInt32.shl_wrap(UInt32(1), UInt32(31)))
    print(UInt64.shl_wrap(UInt64(1), UInt64(63)))

    # shr_wrap signed: logical, NOT sign-extending. Most-negative value
    # is the sharpest test (single sign bit, all other bits zero).
    print(Int8.shr_wrap(Int8(-128), Int8(1)))    # 0x80 >>logical 1 = 64
    print(Int8.shr_wrap(Int8(-1), Int8(1)))
    print(Int16.shr_wrap(Int16(-32768), Int16(1)))
    print(Int32.shr_wrap(Int32(-2147483648), Int32(1)))
    print(Int32.shr_wrap(Int32(-1), Int32(1)))
    print(Int32.shr_wrap(Int32(-256), Int32(8))) # 0xFFFFFF00 >>logical 8 = 0xFFFFFF
    print(Int64.shr_wrap(Int64(-1), Int64(1)))

    # shr_wrap unsigned: matches `>>`
    print(UInt8.shr_wrap(UInt8(0xFF), UInt8(1)))
    print(UInt16.shr_wrap(UInt16(0xFFFF), UInt16(1)))
    print(UInt32.shr_wrap(UInt32(0xFFFFFFFF), UInt32(1)))
    print(UInt64.shr_wrap(UInt64(0xFFFFFFFFFFFFFFFF), UInt64(63)))

    # n=0 round-trip
    print(Int32.shl_wrap(Int32(42), Int32(0)))
    print(Int32.shr_wrap(Int32(-42), Int32(0)))

main()

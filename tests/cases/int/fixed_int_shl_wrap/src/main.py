# Wrapping shifts: Int{8,16,32,64}.{shl,shr}_wrap and the UInt versions.
# shl_wrap silently drops bits shifted out the top (no overflow panic).
# shr_wrap on signed types is logical (zero-fill via unsigned route),
# distinct from `>>` which is arithmetic / sign-extending.
from tpy import int8, int16, int32, int64, uint8, uint16, uint32, uint64

def main() -> None:
    # shl_wrap signed: high bits drop silently
    print(int8.shl_wrap(int8(64), int8(2)))
    print(int8.shl_wrap(int8(1), int8(7)))
    print(int8.shl_wrap(int8(-2), int8(7)))      # 0xFE << 7 -> 0
    print(int16.shl_wrap(int16(0x4000), int16(2)))
    print(int32.shl_wrap(int32(0x40000000), int32(2)))
    print(int64.shl_wrap(int64(1), int64(63)))

    # shl_wrap unsigned
    print(uint8.shl_wrap(uint8(0x80), uint8(1)))
    print(uint16.shl_wrap(uint16(0x8000), uint16(1)))
    print(uint32.shl_wrap(uint32(0x80000000), uint32(1)))
    print(uint32.shl_wrap(uint32(1), uint32(31)))
    print(uint64.shl_wrap(uint64(1), uint64(63)))

    # shr_wrap signed: logical, NOT sign-extending. Most-negative value
    # is the sharpest test (single sign bit, all other bits zero).
    print(int8.shr_wrap(int8(-128), int8(1)))    # 0x80 >>logical 1 = 64
    print(int8.shr_wrap(int8(-1), int8(1)))
    print(int16.shr_wrap(int16(-32768), int16(1)))
    print(int32.shr_wrap(int32(-2147483648), int32(1)))
    print(int32.shr_wrap(int32(-1), int32(1)))
    print(int32.shr_wrap(int32(-256), int32(8))) # 0xFFFFFF00 >>logical 8 = 0xFFFFFF
    print(int64.shr_wrap(int64(-1), int64(1)))

    # shr_wrap unsigned: matches `>>`
    print(uint8.shr_wrap(uint8(0xFF), uint8(1)))
    print(uint16.shr_wrap(uint16(0xFFFF), uint16(1)))
    print(uint32.shr_wrap(uint32(0xFFFFFFFF), uint32(1)))
    print(uint64.shr_wrap(uint64(0xFFFFFFFFFFFFFFFF), uint64(63)))

    # n=0 round-trip
    print(int32.shl_wrap(int32(42), int32(0)))
    print(int32.shr_wrap(int32(-42), int32(0)))

main()

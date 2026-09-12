# Wrapping arithmetic: Int{8,16,32,64}.add_wrap / sub_wrap / mul_wrap
# and UInt{8,16,32,64}.{add,sub,mul}_wrap. Tests boundary overflow.
from tpy import int8, int16, int32, int64, uint8, uint16, uint32, uint64

def main() -> None:
    # Signed overflow: MAX + 1 -> MIN
    print(int8.add_wrap(int8(127), int8(1)))
    print(int16.add_wrap(int16(32767), int16(1)))
    print(int32.add_wrap(int32(2147483647), int32(1)))
    print(int64.add_wrap(int64(9223372036854775807), int64(1)))

    # Signed underflow: MIN - 1 -> MAX
    print(int8.sub_wrap(int8(-128), int8(1)))
    print(int16.sub_wrap(int16(-32768), int16(1)))
    print(int32.sub_wrap(int32(-2147483648), int32(1)))
    print(int64.sub_wrap(int64(-9223372036854775808), int64(1)))

    # Signed mul overflow: bit-pattern-preserving
    print(int8.mul_wrap(int8(127), int8(2)))      # 254 -> -2
    print(int16.mul_wrap(int16(20000), int16(2))) # 40000 -> -25536
    print(int32.mul_wrap(int32(0x40000000), int32(4)))  # 2^30 * 4 = 2^32 -> 0

    # Unsigned overflow: MAX + 1 -> 0
    print(uint8.add_wrap(uint8(255), uint8(1)))
    print(uint16.add_wrap(uint16(65535), uint16(1)))
    print(uint32.add_wrap(uint32(4294967295), uint32(1)))
    print(uint64.add_wrap(uint64(18446744073709551615), uint64(1)))

    # Unsigned underflow: 0 - 1 -> MAX
    print(uint8.sub_wrap(uint8(0), uint8(1)))
    print(uint16.sub_wrap(uint16(0), uint16(1)))
    print(uint32.sub_wrap(uint32(0), uint32(1)))
    print(uint64.sub_wrap(uint64(0), uint64(1)))

    # Unsigned mul overflow
    print(uint8.mul_wrap(uint8(16), uint8(16)))      # 256 -> 0
    print(uint32.mul_wrap(uint32(0x80000000), uint32(2)))  # 2^31 * 2 -> 0

    # Non-overflowing cases still work
    print(int32.add_wrap(int32(5), int32(7)))
    print(uint32.mul_wrap(uint32(100), uint32(200)))

main()

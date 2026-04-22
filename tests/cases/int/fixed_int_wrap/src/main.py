# Wrapping arithmetic: Int{8,16,32,64}.add_wrap / sub_wrap / mul_wrap
# and UInt{8,16,32,64}.{add,sub,mul}_wrap. Tests boundary overflow.
from tpy import Int8, Int16, Int32, Int64, UInt8, UInt16, UInt32, UInt64

def main() -> None:
    # Signed overflow: MAX + 1 -> MIN
    print(Int8.add_wrap(Int8(127), Int8(1)))
    print(Int16.add_wrap(Int16(32767), Int16(1)))
    print(Int32.add_wrap(Int32(2147483647), Int32(1)))
    print(Int64.add_wrap(Int64(9223372036854775807), Int64(1)))

    # Signed underflow: MIN - 1 -> MAX
    print(Int8.sub_wrap(Int8(-128), Int8(1)))
    print(Int16.sub_wrap(Int16(-32768), Int16(1)))
    print(Int32.sub_wrap(Int32(-2147483648), Int32(1)))
    print(Int64.sub_wrap(Int64(-9223372036854775808), Int64(1)))

    # Signed mul overflow: bit-pattern-preserving
    print(Int8.mul_wrap(Int8(127), Int8(2)))      # 254 -> -2
    print(Int16.mul_wrap(Int16(20000), Int16(2))) # 40000 -> -25536
    print(Int32.mul_wrap(Int32(0x40000000), Int32(4)))  # 2^30 * 4 = 2^32 -> 0

    # Unsigned overflow: MAX + 1 -> 0
    print(UInt8.add_wrap(UInt8(255), UInt8(1)))
    print(UInt16.add_wrap(UInt16(65535), UInt16(1)))
    print(UInt32.add_wrap(UInt32(4294967295), UInt32(1)))
    print(UInt64.add_wrap(UInt64(18446744073709551615), UInt64(1)))

    # Unsigned underflow: 0 - 1 -> MAX
    print(UInt8.sub_wrap(UInt8(0), UInt8(1)))
    print(UInt16.sub_wrap(UInt16(0), UInt16(1)))
    print(UInt32.sub_wrap(UInt32(0), UInt32(1)))
    print(UInt64.sub_wrap(UInt64(0), UInt64(1)))

    # Unsigned mul overflow
    print(UInt8.mul_wrap(UInt8(16), UInt8(16)))      # 256 -> 0
    print(UInt32.mul_wrap(UInt32(0x80000000), UInt32(2)))  # 2^31 * 2 -> 0

    # Non-overflowing cases still work
    print(Int32.add_wrap(Int32(5), Int32(7)))
    print(UInt32.mul_wrap(UInt32(100), UInt32(200)))

main()

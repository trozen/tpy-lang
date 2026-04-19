# tpy.bits: 32/64-bit rotations (left/right) and byte swap.
# Wraps C++20 std::rotl/std::rotr and C++23 std::byteswap.
from tpy import UInt32, UInt64
from tpy.bits import rotl32, rotr32, rotl64, rotr64, byteswap32, byteswap64

def main() -> None:
    # Simple rotations
    print(rotl32(UInt32(1), 1))                    # 2
    print(rotl32(UInt32(1), 31))                   # 0x80000000
    print(rotl32(UInt32(0x80000000), 1))           # 1 (bit wraps)
    print(rotr32(UInt32(1), 1))                    # 0x80000000
    print(rotr32(UInt32(0x80000000), 1))           # 0x40000000
    print(rotr32(UInt32(0x12345678), 8))           # 0x78123456

    # Rotation by 0 and by width: identity
    print(rotl32(UInt32(0xdeadbeef), 0))           # 0xdeadbeef
    print(rotr32(UInt32(0xdeadbeef), 32))          # 0xdeadbeef

    # 64-bit rotations
    print(rotl64(UInt64(1), 1))                    # 2
    print(rotl64(UInt64(1), 63))                   # 0x8000000000000000
    print(rotr64(UInt64(1), 1))                    # 0x8000000000000000
    print(rotr64(UInt64(0x0123456789abcdef), 16))  # 0xcdef0123456789ab

    # Byte swap: 32-bit
    print(byteswap32(UInt32(0x12345678)))          # 0x78563412
    print(byteswap32(UInt32(0x00000001)))          # 0x01000000
    print(byteswap32(UInt32(0xff00ff00)))          # 0x00ff00ff

    # Byte swap: 64-bit
    print(byteswap64(UInt64(0x0123456789abcdef)))  # 0xefcdab8967452301
    print(byteswap64(UInt64(0x00000000ffffffff)))  # 0xffffffff00000000

main()

# Comprehensive BigInt operation coverage around the int63 small/big boundary.
# Exercises arithmetic, bitwise, shifts, comparisons, pow, and int conversions.

from tpy import Int32, Int64


def probe(label: str, a: int, b: int, shift: Int32) -> None:
    print("===")
    print(label)
    print("===")
    print(a)
    print(b)
    print(a + b)
    print(a - b)
    print(a * b)
    if b != 0:
        print(a // b)
        print(a % b)
    print(a & b)
    print(a | b)
    print(a ^ b)
    print(a << shift)
    print(a >> shift)
    print(-a)
    print(~a)
    print(a == b)
    print(a != b)
    print(a < b)
    print(a <= b)
    print(a > b)
    print(a >= b)


B: int = 1 << 62
SMALL_MAX: int = B - 1
SMALL_MIN: int = -B
BIG_POS: int = B
BIG_NEG: int = -B - 1
WIDE_POS: int = 1 << 70
WIDE_NEG: int = -(1 << 70)
ONE_BIG: int = 1

# Boundary transitions around int63 small-int optimization.
print(SMALL_MAX)
print(SMALL_MAX + 1)
print(BIG_POS - 1)
print(SMALL_MIN)
print(SMALL_MIN - 1)
print(BIG_NEG + 1)
print(((B + 1) - 2))
print(((-B - 1) + 2))

# Core operation matrix with mixed signs and magnitudes.
probe("small_max + one", SMALL_MAX, 1, Int32(1))
probe("small_min + minus_one", SMALL_MIN, -1, Int32(1))
probe("big_pos + three", BIG_POS, 3, Int32(2))
probe("big_neg + three", BIG_NEG, 3, Int32(2))
probe("wide_pos + minus_five", WIDE_POS, -5, Int32(31))
probe("wide_neg + seven", WIDE_NEG, 7, Int32(31))
probe("zero + big_pos", 0, BIG_POS, Int32(63))
probe("minus_one + wide_pos", -1, WIDE_POS, Int32(64))

# Shift behavior at and beyond machine-word boundaries.
print(BIG_POS << 63)
print(BIG_POS >> 63)
print(BIG_NEG << 63)
print(BIG_NEG >> 63)
print((-1) >> 200)
print((ONE_BIG << 200) >> 199)
shift_count: int = 65
print(BIG_POS << shift_count)
print(BIG_NEG >> shift_count)

# Power behavior with BigInt values.
print((B + 1) ** 2)
print((-B - 1) ** 3)
print((-(B + 1)) ** 4)
print((WIDE_NEG) ** 0)
print((WIDE_POS // (ONE_BIG << 60)) ** 6)

# Constructor conversions from BigInt to fixed-width integers.
in_i32_max: int = (ONE_BIG << 31) - 1
in_i32_min: int = -(ONE_BIG << 31)
in_i64_from_big: int = (ONE_BIG << 62) + 123
print(Int32(in_i32_max))
print(Int32(in_i32_min))
print(Int64(in_i64_from_big))
print(Int64(-in_i64_from_big))

# String and float conversions through int() constructor.
print(int("  +123456789012345678901234567890  "))
print(int(" -999999999999999999999999999999 "))
print(int(1.9e20))
print(int(-1.9e20))

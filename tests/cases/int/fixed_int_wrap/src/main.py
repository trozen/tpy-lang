# Wrapping fixed-width spellings: T.wrap(x) reduces x modulo 2**bits into T,
# T.wrap_add / wrap_sub / wrap_mul reduce the Python result the same way, and
# T.wrap_shl / wrap_shr take the count modulo bits (a signed wrap_shr is
# arithmetic, as `>>` is). Each output line names its section.
from typing import Iterator

from tpy import int8, int16, int32, int64, uint8, uint16, uint32, uint64


# free function: wrap from a fixed int and an int literal
def conversions() -> None:
    x = 300
    print("wrap fixed", uint8.wrap(x), uint8.wrap(int8(-1)), uint8.wrap(int8(-3)))  # tpyc: ok
    print("wrap fixed", uint8.wrap(uint16(1000)), int8.wrap(int32(200)), int8.wrap(uint8(200)))  # tpyc: ok
    print("wrap fixed", uint8.wrap(int32(42)), int8.wrap(int16(-100)), uint64.wrap(int8(-1)))  # tpyc: ok
    print("wrap literal", uint8.wrap(300), uint8.wrap(-1), int8.wrap(-129))  # tpyc: ok


# free function: every width's add / sub / mul at the extremes
def arithmetic() -> None:
    print("arith int8", int8.wrap_add(127, 1), int8.wrap_sub(-128, 1),  # tpyc: ok
          int8.wrap_mul(127, 2), int8.wrap_mul(-128, -1), int8.wrap_mul(-1, -1),
          int8.wrap_mul(-128, -128))
    print("arith int16", int16.wrap_add(32767, 1), int16.wrap_sub(-32768, 1),  # tpyc: ok
          int16.wrap_mul(20000, 2), int16.wrap_mul(-32768, -1), int16.wrap_mul(-1, -1),
          int16.wrap_mul(-32768, -32768), int16.wrap_mul(32767, 32767))
    print("arith int32", int32.wrap_add(2147483647, 1), int32.wrap_sub(-2147483648, 1),  # tpyc: ok
          int32.wrap_mul(0x40000000, 4), int32.wrap_mul(-2147483648, -1),
          int32.wrap_mul(2147483647, 2147483647))
    print("arith int64", int64.wrap_add(9223372036854775807, 1),  # tpyc: ok
          int64.wrap_sub(-9223372036854775808, 1),
          int64.wrap_mul(-9223372036854775808, -1),
          int64.wrap_mul(9223372036854775807, 9223372036854775807))
    print("arith uint8", uint8.wrap_add(255, 1), uint8.wrap_sub(0, 1),  # tpyc: ok
          uint8.wrap_mul(16, 16), uint8.wrap_mul(255, 255))
    print("arith uint16", uint16.wrap_add(65535, 1), uint16.wrap_sub(0, 1),  # tpyc: ok
          uint16.wrap_mul(65535, 65535), uint16.wrap_mul(256, 256))
    print("arith uint32", uint32.wrap_add(4294967295, 1), uint32.wrap_sub(0, 1),  # tpyc: ok
          uint32.wrap_mul(0x80000000, 2), uint32.wrap_mul(4294967295, 4294967295))
    print("arith uint64", uint64.wrap_add(18446744073709551615, 1), uint64.wrap_sub(0, 1),  # tpyc: ok
          uint64.wrap_mul(18446744073709551615, 18446744073709551615))
    print("arith plain", int32.wrap_add(5, 7), uint32.wrap_mul(100, 200))


# free function: shift counts 0, bits-1, bits, bits+1 and -1 at every width
def shifts() -> None:
    print("shl int8", int8.wrap_shl(1, 0), int8.wrap_shl(1, 7), int8.wrap_shl(1, 8),  # tpyc: ok
          int8.wrap_shl(1, 9), int8.wrap_shl(1, -1), int8.wrap_shl(-2, 7))
    print("shl int16", int16.wrap_shl(1, 0), int16.wrap_shl(1, 15), int16.wrap_shl(1, 16),
          int16.wrap_shl(1, 17), int16.wrap_shl(1, -1))
    print("shl int32", int32.wrap_shl(1, 0), int32.wrap_shl(1, 31), int32.wrap_shl(1, 32),
          int32.wrap_shl(1, 33), int32.wrap_shl(1, -1))
    print("shl int64", int64.wrap_shl(1, 0), int64.wrap_shl(1, 63), int64.wrap_shl(1, 64),
          int64.wrap_shl(1, 65), int64.wrap_shl(1, -1))
    print("shl uint8", uint8.wrap_shl(1, 0), uint8.wrap_shl(1, 7), uint8.wrap_shl(1, 8),
          uint8.wrap_shl(1, 9), uint8.wrap_shl(1, -1), uint8.wrap_shl(0x80, 1))
    print("shl uint16", uint16.wrap_shl(1, 0), uint16.wrap_shl(1, 15), uint16.wrap_shl(1, 16),
          uint16.wrap_shl(1, 17), uint16.wrap_shl(1, -1))
    print("shl uint32", uint32.wrap_shl(1, 0), uint32.wrap_shl(1, 31), uint32.wrap_shl(1, 32),
          uint32.wrap_shl(1, 33), uint32.wrap_shl(1, -1))
    print("shl uint64", uint64.wrap_shl(1, 0), uint64.wrap_shl(1, 63), uint64.wrap_shl(1, 64),
          uint64.wrap_shl(1, 65), uint64.wrap_shl(1, -1))
    print("shr uint8", uint8.wrap_shr(0xFF, 0), uint8.wrap_shr(0xFF, 7), uint8.wrap_shr(0xFF, 8),
          uint8.wrap_shr(0xFF, 9), uint8.wrap_shr(0xFF, -1))
    print("shr uint16", uint16.wrap_shr(0xFFFF, 15), uint16.wrap_shr(0xFFFF, 16),
          uint16.wrap_shr(0xFFFF, 17), uint16.wrap_shr(0xFFFF, -1))
    print("shr uint32", uint32.wrap_shr(0xFFFFFFFF, 31), uint32.wrap_shr(0xFFFFFFFF, 32),
          uint32.wrap_shr(0xFFFFFFFF, 33), uint32.wrap_shr(0xFFFFFFFF, -1))
    print("shr uint64", uint64.wrap_shr(0xFFFFFFFFFFFFFFFF, 63), uint64.wrap_shr(0xFFFFFFFFFFFFFFFF, 64),
          uint64.wrap_shr(0xFFFFFFFFFFFFFFFF, 65), uint64.wrap_shr(0xFFFFFFFFFFFFFFFF, -1))
    # a signed wrap_shr of a negative value sign-extends
    print("shr int8", int8.wrap_shr(-128, 1), int8.wrap_shr(-1, 1), int8.wrap_shr(-128, 7),  # tpyc: ok
          int8.wrap_shr(-128, 8), int8.wrap_shr(-128, 9), int8.wrap_shr(-128, -1))
    print("shr int16", int16.wrap_shr(-32768, 1), int16.wrap_shr(-32768, 15),
          int16.wrap_shr(-32768, 16), int16.wrap_shr(-32768, 17), int16.wrap_shr(-32768, -1))
    print("shr int32", int32.wrap_shr(-256, 8), int32.wrap_shr(-1, 31), int32.wrap_shr(-256, 32),
          int32.wrap_shr(-256, 33), int32.wrap_shr(-256, -1))
    print("shr int64", int64.wrap_shr(-1, 1), int64.wrap_shr(-9223372036854775808, 63),
          int64.wrap_shr(-256, 64), int64.wrap_shr(-256, 65), int64.wrap_shr(-256, -1))


# free function: the count keeps its own fixed type
def shift_count_types(k8: int8, k64: uint64) -> None:
    print("count types", uint32.wrap_shl(3, k8), int16.wrap_shr(-4096, k64))  # tpyc: ok
    wide = k64 + 50
    print("count types", int16.wrap_shr(-4096, wide), uint8.wrap_shl(1, wide))  # tpyc: ok



def take(s: list[int8]) -> int8:
    v = s[0]
    s[0] += 1
    return v


# free function: a shift evaluates its value before its count, as Python does
def shift_operand_order() -> None:
    s: list[int8] = [2]
    a = int8.wrap_shl(take(s), take(s))  # tpyc: ok
    t: list[int8] = [1]
    b = int8.wrap_shr(take(t), take(t))  # tpyc: ok
    print("order", a, b)


# free function: an operand outside T converts with a check first, as int8(x) does
def operand_out_of_range(big: int32) -> None:
    try:
        r = int8.wrap_add(1, big)  # tpyc: ok
        print("operand", r)
    except OverflowError:
        print("operand OverflowError")


class Hasher:
    h: list[uint32]
    acc: uint32

    def __init__(self) -> None:
        self.h = [4294967295, 7]
        self.acc = 2166136261

    # method: a field operand and a subscript operand
    def mix(self, c: uint32) -> None:
        self.h[0] = uint32.wrap_add(self.h[0], c)  # tpyc: ok
        self.acc = uint32.wrap_mul(self.acc, 16777619)  # tpyc: ok
        self.h[1] = uint32.wrap_shl(self.h[1], self.h[0])  # tpyc: ok


def method() -> None:
    hs = Hasher()
    hs.mix(3)
    hs.mix(0xFFFFFFFF)
    print("method", hs.h[0], hs.h[1], hs.acc)


# generator: wrapping inside a resumable body
def wrapped_sums(xs: list[uint8]) -> Iterator[uint8]:
    for x in xs:
        yield uint8.wrap_add(x, 200)  # tpyc: ok


def generator() -> None:
    print("generator", list(wrapped_sums([0, 55, 56, 255])))


# comprehension: wrapping in the element expression
def comprehension() -> None:
    vals: list[uint16] = [1, 255, 256, 65535]
    print("comprehension", [uint16.wrap_mul(v, v) for v in vals])  # tpyc: ok
    print("comprehension", [int8.wrap(v) for v in vals])  # tpyc: ok


def main() -> None:
    conversions()
    arithmetic()
    shifts()
    shift_count_types(-1, 20)
    operand_out_of_range(256)
    shift_operand_order()
    method()
    generator()
    comprehension()


main()

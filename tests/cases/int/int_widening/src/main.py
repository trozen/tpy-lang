# Test implicit widening coercions between fixed-width integer types
from tpy import int8, int16, int32, int64, uint8, uint16, uint32, uint64

def print_i16(x: int16) -> None:
    print(x)

def print_i32(x: int32) -> None:
    print(x)

def print_i64(x: int64) -> None:
    print(x)

def print_u16(x: uint16) -> None:
    print(x)

def print_u32(x: uint32) -> None:
    print(x)

def print_u64(x: uint64) -> None:
    print(x)

def main() -> None:
    # Signed widening: int8 -> int16 -> int32 -> int64
    a: int8 = int8(42)
    print_i16(a)
    print_i32(a)
    print_i64(a)

    b: int16 = int16(1000)
    print_i32(b)
    print_i64(b)

    c: int32 = int32(100000)
    print_i64(c)

    # Unsigned widening: uint8 -> uint16 -> uint32 -> uint64
    d: uint8 = uint8(200)
    print_u16(d)
    print_u32(d)
    print_u64(d)

    e: uint16 = uint16(50000)
    print_u32(e)
    print_u64(e)

    f: uint32 = uint32(3000000000)
    print_u64(f)

    # Cross-sign widening: uint8 -> int16, uint16 -> int32, uint32 -> int64
    print_i16(d)   # uint8(200) -> int16
    print_i32(d)   # uint8(200) -> int32

    g: uint16 = uint16(60000)
    print_i32(g)   # uint16(60000) -> int32

    print_i64(f)   # uint32(3000000000) -> int64

main()

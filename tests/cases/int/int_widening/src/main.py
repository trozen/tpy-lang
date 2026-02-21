# Test implicit widening coercions between fixed-width integer types
from tpy import Int8, Int16, Int32, Int64, UInt8, UInt16, UInt32, UInt64

def print_i16(x: Int16) -> None:
    print(x)

def print_i32(x: Int32) -> None:
    print(x)

def print_i64(x: Int64) -> None:
    print(x)

def print_u16(x: UInt16) -> None:
    print(x)

def print_u32(x: UInt32) -> None:
    print(x)

def print_u64(x: UInt64) -> None:
    print(x)

def main() -> None:
    # Signed widening: Int8 -> Int16 -> Int32 -> Int64
    a: Int8 = Int8(42)
    print_i16(a)
    print_i32(a)
    print_i64(a)

    b: Int16 = Int16(1000)
    print_i32(b)
    print_i64(b)

    c: Int32 = Int32(100000)
    print_i64(c)

    # Unsigned widening: UInt8 -> UInt16 -> UInt32 -> UInt64
    d: UInt8 = UInt8(200)
    print_u16(d)
    print_u32(d)
    print_u64(d)

    e: UInt16 = UInt16(50000)
    print_u32(e)
    print_u64(e)

    f: UInt32 = UInt32(3000000000)
    print_u64(f)

    # Cross-sign widening: UInt8 -> Int16, UInt16 -> Int32, UInt32 -> Int64
    print_i16(d)   # UInt8(200) -> Int16
    print_i32(d)   # UInt8(200) -> Int32

    g: UInt16 = UInt16(60000)
    print_i32(g)   # UInt16(60000) -> Int32

    print_i64(f)   # UInt32(3000000000) -> Int64

main()

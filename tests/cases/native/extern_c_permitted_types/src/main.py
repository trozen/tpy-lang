# Regression guard for the PERMITTED side of the C-ABI allow-list: fixed-width
# ints, Float32, float, bool, Char and Ptr[T] must stay legal in a C-linkage
# signature, in both param and return position, plus `None` (void) as a return.
# Narrowing the allow-list has to fail here.
from tpy.extern import export
from tpy import (Int8, Int16, Int32, Int64, UInt8, UInt16, UInt32, UInt64,
                 Float32, Char, Ptr)
from tpy.unsafe import unsafe_load, unsafe_ptr

# Every permitted family at once in param position, returning None (void).
@export(binding="C")
def sink(i8: Int8, i16: Int16, i32: Int32, i64: Int64,
         u8: UInt8, u16: UInt16, u32: UInt32, u64: UInt64,
         f32: Float32, d: float, flag: bool, ch: Char,
         p: Ptr[Int32]) -> None:
    print(i8)
    print(i16)
    print(i32)
    print(i64)
    print(u8)
    print(u16)
    print(u32)
    print(u64)
    print(f32)
    print(d)
    print(flag)
    print(ch)
    print(unsafe_load(p, 0))

# The same families in return position -- the gate checks returns separately.
@export(binding="C")
def echo_i64(x: Int64) -> Int64:
    return x

@export(binding="C")
def echo_f32(x: Float32) -> Float32:
    return x

@export(binding="C")
def echo_bool(x: bool) -> bool:
    return x

@export(binding="C")
def echo_char(x: Char) -> Char:
    return x

@export(binding="C")
def echo_ptr(p: Ptr[Int32]) -> Ptr[Int32]:
    return p

def main() -> None:
    xs = [Int32(7), Int32(8)]
    p = unsafe_ptr(xs)
    sink(Int8(-1), Int16(-2), Int32(-3), Int64(-4),
         UInt8(1), UInt16(2), UInt32(3), UInt64(4),
         Float32(0.5), 1.25, True, Char('z'), p)
    print(echo_i64(Int64(99)))
    print(echo_f32(Float32(1.5)))
    print(echo_bool(False))
    print(echo_char(Char('q')))
    print(unsafe_load(echo_ptr(p), 1))

main()

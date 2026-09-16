# Regression guard for the PERMITTED side of the C-ABI allow-list: fixed-width
# ints, float32, float, bool, char and Ptr[T] must stay legal in a C-linkage
# signature, in both param and return position, plus `None` (void) as a return
# and Own over a value type (the owned and borrowed C++ forms coincide there,
# so Own says nothing about the ABI). Narrowing the allow-list has to fail here.
from tpy.extern import export
from tpy import (int8, int16, int32, int64, uint8, uint16, uint32, uint64,
                 float32, char, Own, Ptr)
from tpy.unsafe import unsafe_load, unsafe_ptr

# Every permitted family at once in param position, returning None (void).
@export(binding="C")
def sink(i8: int8, i16: int16, i32: int32, i64: int64,
         u8: uint8, u16: uint16, u32: uint32, u64: uint64,
         f32: float32, d: float, flag: bool, ch: char,
         p: Ptr[int32]) -> None:
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
def echo_i64(x: int64) -> int64:
    return x

@export(binding="C")
def echo_f32(x: float32) -> float32:
    return x

@export(binding="C")
def echo_bool(x: bool) -> bool:
    return x

@export(binding="C")
def echo_char(x: char) -> char:
    return x

@export(binding="C")
def echo_ptr(p: Ptr[int32]) -> Ptr[int32]:
    return p

# Own over a value type, at both positions.
@export(binding="C")
def doubled(x: Own[int32]) -> Own[int32]:  # tpyc: ok
    return x * 2

def main() -> None:
    xs = [int32(7), int32(8)]
    p = unsafe_ptr(xs)
    sink(int8(-1), int16(-2), int32(-3), int64(-4),
         uint8(1), uint16(2), uint32(3), uint64(4),
         float32(0.5), 1.25, True, char('z'), p)
    print(echo_i64(int64(99)))
    print(echo_f32(float32(1.5)))
    print(echo_bool(False))
    print(echo_char(char('q')))
    print(unsafe_load(echo_ptr(p), 1))
    print(doubled(21))

main()

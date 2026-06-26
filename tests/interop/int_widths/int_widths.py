# tpy: ext_module
# CPython extension over the fixed-width int boundary: each width round-trips
# through its C++ type (int8_t..int64_t / uint8_t..uint64_t) with a per-width
# range check at the boundary. In-range values match the TPy source under
# CPython (where the annotation is unbounded and unchecked); out-of-range or
# negative-into-unsigned values raise OverflowError only against the compiled
# .so, so those live in ext_checks.py.
from tpy import Int8, UInt8, Int16, UInt16, Int32, UInt32, Int64, UInt64
from tpy.extern import export


@export
def i8(x: Int8) -> Int8:
    return x


@export
def u8(x: UInt8) -> UInt8:
    return x


@export
def i16(x: Int16) -> Int16:
    return x


@export
def u16(x: UInt16) -> UInt16:
    return x


@export
def i32(x: Int32) -> Int32:
    return x


@export
def u32(x: UInt32) -> UInt32:
    return x


@export
def i64(x: Int64) -> Int64:
    return x


@export
def u64(x: UInt64) -> UInt64:
    return x

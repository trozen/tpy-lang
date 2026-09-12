# tpy: ext_module
# CPython extension over the fixed-width int boundary: each width round-trips
# through its C++ type (int8_t..int64_t / uint8_t..uint64_t) with a per-width
# range check at the boundary. In-range values match the TPy source under
# CPython (where the annotation is unbounded and unchecked); out-of-range or
# negative-into-unsigned values raise OverflowError only against the compiled
# .so, so those live in ext_checks.py.
from tpy import int8, uint8, int16, uint16, int32, uint32, int64, uint64
from tpy.extern import export


@export
def i8(x: int8) -> int8:
    return x


@export
def u8(x: uint8) -> uint8:
    return x


@export
def i16(x: int16) -> int16:
    return x


@export
def u16(x: uint16) -> uint16:
    return x


@export
def i32(x: int32) -> int32:
    return x


@export
def u32(x: uint32) -> uint32:
    return x


@export
def i64(x: int64) -> int64:
    return x


@export
def u64(x: uint64) -> uint64:
    return x

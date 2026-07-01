# tpy: ext_module
# Span[T] numeric marshalling across the @export boundary (docs/
# CPYTHON_INTEROP.md "buffer protocol"): a Span[readonly[T]]/Span[T] param
# binds to any buffer-protocol exporter (array.array, memoryview, bytes,
# bytearray) via PyObject_GetBuffer, format/itemsize checked against T. v1 is
# copy-in only -- there is no write-back, so a mutation through a Span[T]
# (non-readonly) param is not visible to the caller (proven in
# ext_checks.py); Span never crosses as a return type (use list[T]).
from tpy import Span, readonly, Int32, Int64, UInt8, UInt32
from tpy.extern import export


@export
def sum_floats(data: Span[readonly[float]]) -> float:
    s = 0.0
    for x in data:
        s += x
    return s


@export
def sum_int32(data: Span[readonly[Int32]]) -> Int64:
    s: Int64 = 0
    for x in data:
        s += Int64(x)
    return s


@export
def sum_uint8(data: Span[readonly[UInt8]]) -> UInt32:
    s: UInt32 = 0
    for x in data:
        s += UInt32(x)
    return s


@export
def peek_int32(data: Span[Int32]) -> Int64:
    # Same read-only body as sum_int32, but the param is the MUTABLE form
    # (no readonly) -- proves the mutable form marshals correctly across
    # source types, and that a read-only use of it doesn't warn (see
    # tests/cases/interop/warn_export_span_mutation).
    s: Int64 = 0
    for x in data:
        s += Int64(x)
    return s


@export
def scale_in_place(data: Span[Int32], factor: Int32) -> None:
    # Mutates a copy-in param: the boundary warns (see tests/cases/interop/
    # warn_export_span_mutation), and the mutation is not visible to the
    # caller (proven in ext_checks.py). Aliases in the source.
    for i in range(len(data)):
        data[i] = data[i] * factor

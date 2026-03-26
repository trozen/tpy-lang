# tpy: native_module(forward=True)
# tpy: cpp_namespace("tpystd::tpy")
from ._core import (
    # Structural protocols
    Truthy, Stringable, Representable, Hashable, Comparable, Equatable,
    Deref, ReadOnlySpanLike,
    # Marker protocols
    NativeIterable, NativeRangeConstructible, ValueType, Send, Sync,
    Default, Covariant,
    # Fixed-width integer constraint protocols
    AnyFixedInt, AnyFixedSigned, AnyFixedUnsigned,
    # Primitive types
    Float32,
    Int8, Int16, Int32, Int64,
    UInt8, UInt16, UInt32, UInt64,
    Char, String, StrView, BytesView,
    # Container types
    Span, Array, Ptr, SpanIter,
    # Functions
    span, deref, take_ptr, make_default,
)
from ._bootstrap import (
    # Decorators / type modifiers
    readonly, noalloc, nocopy, pure, dynamic, error_return,
    Own, Fn,
)

type Float64 = float

__all__ = [
    # Primitive types
    "Int8", "Int16", "Int32", "Int64",
    "UInt8", "UInt16", "UInt32", "UInt64",
    "Float32", "Float64",
    "Char", "String", "StrView", "BytesView",
    # Container types
    "Span", "Array", "Ptr", "SpanIter",
    # Pointer / ownership
    "Own", "Fn",
    # Decorators / type modifiers
    "readonly", "noalloc", "nocopy", "pure", "dynamic", "error_return",
    "auto_readonly", "auto_own",  # parser keywords (no .py stub)
    # Structural protocols
    "Truthy", "Stringable", "Representable",
    "Hashable", "Comparable", "Equatable",
    "Deref", "ReadOnlySpanLike", "Default",
    # Marker protocols
    "NativeIterable", "NativeRangeConstructible",
    "ValueType", "Send", "Sync", "Covariant",
    # Fixed-width integer constraint protocols
    "AnyFixedInt", "AnyFixedSigned", "AnyFixedUnsigned",
    # Functions
    "span", "deref", "take_ptr", "make_default",
]

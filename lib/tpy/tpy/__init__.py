# tpy: native_module
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
    Char, String, StrView,
    # Container types
    Span, Array, Ptr, SpanIter,
    # Functions
    span, deref, take_ptr, make_default,
    # Decorators / type modifiers
    readonly, noalloc, nocopy, pure, dynamic,
    auto_readonly, auto_own, error_return,
)

type Float64 = float

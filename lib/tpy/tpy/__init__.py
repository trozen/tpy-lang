# tpy: native_module
# tpy: cpp_namespace("tpystd::tpy")
# _bootstrap must be imported before _core: _bootstrap._extern defines
# @builtin_decorator stubs whose arg schemas are needed when parsing
# decorator kwargs (e.g. @native(function=True)) in _core/_types.py.
from ._bootstrap import (
    # Decorators / type modifiers
    readonly, noalloc, nocopy, pure, inline, dynamic, error_return,
    Own, Fn,
)
from ._core import (
    # Structural protocols
    Truthy, Stringable, Representable, Hashable, Comparable, Equatable,
    Deref, Spannable, Writable, Readable, BinaryWritable, BinaryReadable,
    Seekable, Closable,
    # Marker protocols
    NativeIterable, NativeRangeConstructible, ValueType, Send, Sync,
    Default, Covariant, ReturnException,
    # Fixed-width integer constraint protocols
    AnyFixedInt, AnyFixedSigned, AnyFixedUnsigned,
    # Primitive types
    Float32,
    Int8, Int16, Int32, Int64,
    UInt8, UInt16, UInt32, UInt64,
    Char, String, StrView, FStr, BytesView,
    # Container types
    Span, Array, Ptr, SpanIter,
    # Functions
    span, deref, take_ptr, make_default,
    copy, copy_iter, own_iter, try_parse,
)
from ._builtins._types import basic_slice
from ._builtins._io import BinaryIO, open_text, open_binary

type Float64 = float

# Version/implementation identification lives in `tpy.version` submodule:
#     from tpy.version import __version__, version_info, is_compiled
# Not re-exported here because native_module facades don't propagate
# variable imports or transitive init chains.

__all__ = [
    # Primitive types
    "Int8", "Int16", "Int32", "Int64",
    "UInt8", "UInt16", "UInt32", "UInt64",
    "Float32", "Float64",
    "Char", "String", "StrView", "FStr", "BytesView",
    # Container types
    "Span", "Array", "Ptr", "SpanIter",
    # Slice types
    "basic_slice",
    # Pointer / ownership
    "Own", "Fn",
    # Decorators / type modifiers
    "readonly", "noalloc", "nocopy", "pure", "inline", "dynamic", "error_return",
    "auto_readonly", "auto_own",  # parser keywords (no .py stub)
    # Structural protocols
    "Truthy", "Stringable", "Representable",
    "Hashable", "Comparable", "Equatable",
    "Deref", "Spannable", "Writable", "Readable",
    "BinaryWritable", "BinaryReadable",
    "Seekable", "Closable", "Default",
    # Marker protocols
    "NativeIterable", "NativeRangeConstructible",
    "ValueType", "Send", "Sync", "Covariant", "ReturnException",
    # Fixed-width integer constraint protocols
    "AnyFixedInt", "AnyFixedSigned", "AnyFixedUnsigned",
    # Functions
    "span", "deref", "take_ptr", "make_default",
    "copy", "copy_iter", "own_iter", "try_parse",
    # I/O
    "BinaryIO", "open_text", "open_binary",
]

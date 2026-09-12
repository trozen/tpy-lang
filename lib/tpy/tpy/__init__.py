# tpy: native_module
# tpy: cpp_namespace("tpystd::tpy")
# _bootstrap must be imported before _core: _bootstrap._extern defines
# @builtin_decorator stubs whose arg schemas are needed when parsing
# decorator kwargs (e.g. @native(function=True)) in _core/_types.py.
from ._bootstrap import (
    # Decorators / type modifiers
    readonly, noalloc, hotpath, nocopy, pure, inline, dispatch, dynamic, error_return,
    unsafe_send, unsafe_sync, nosend, nosync, nomove,
    Own, Fn,
)
from ._core import (
    # Structural protocols
    Truthy, Stringable, Representable, Hashable, Comparable, Equatable,
    Deref, Spannable, Writable, Readable, BinaryWritable, BinaryReadable,
    Seekable, Closable,
    # Marker protocols
    NativeIterable, NativeRangeConstructible, ValueType, Copyable, Send, Sync,
    Default, Covariant, ReturnException,
    # Fixed-width integer constraint protocols
    AnyFixedInt, AnyFixedSigned, AnyFixedUnsigned,
    # Primitive types
    float32,
    int8, int16, int32, int64,
    uint8, uint16, uint32, uint64,
    char, String, StrView, FStr, BytesView,
    # Container types
    Span, Array, Ptr, SpanIter,
    # varargs is imported under a private alias so its builtin record
    # propagates to the `tpy` root (registered by builtin_type_key, for
    # *args body-op resolution) without exposing the user-spellable name --
    # the *args body view is internal-only.
    varargs as _varargs,
    # Functions
    span, deref, take_ptr, make_default,
    copy, copy_iter, own_iter, try_parse,
    assert_send, assert_sync,
)
# `Task` / `Waker` / `Poll` / `Awaitable` / `poll_*` are not re-exported
# here -- importing from `tpy` would force every consumer to include
# `tpystd/coro.hpp` transitively. Use `from tpy.coro import ...`.
from ._builtins._exceptions import CancelledError
from ._builtins._types import basic_slice
from ._builtins._io import BinaryIO, open_text, open_binary

type float64 = float

# Version/implementation identification lives in `tpy.version` submodule:
#     from tpy.version import __version__, version_info, is_compiled
# Not re-exported here because native_module facades don't propagate
# variable imports or transitive init chains.

__all__ = [
    # Primitive types
    "int8", "int16", "int32", "int64",
    "uint8", "uint16", "uint32", "uint64",
    "float32", "float64",
    "char", "String", "StrView", "FStr", "BytesView",
    # Container types
    "Span", "Array", "Ptr", "SpanIter",
    # Slice types
    "basic_slice",
    # Pointer / ownership
    "Own", "Fn",
    # Decorators / type modifiers
    "readonly", "noalloc", "hotpath", "nocopy", "pure", "inline", "dispatch", "dynamic", "error_return",
    "unsafe_send", "unsafe_sync", "nosend", "nosync", "nomove",
    "auto_readonly", "auto_own", "unsafe_interior_mutable",  # parser keywords (no .py stub)
    # Structural protocols
    "Truthy", "Stringable", "Representable",
    "Hashable", "Comparable", "Equatable",
    "Deref", "Spannable", "Writable", "Readable",
    "BinaryWritable", "BinaryReadable",
    "Seekable", "Closable", "Default",
    # Marker protocols
    "NativeIterable", "NativeRangeConstructible",
    "ValueType", "Copyable", "Send", "Sync", "Covariant", "ReturnException",
    # Fixed-width integer constraint protocols
    "AnyFixedInt", "AnyFixedSigned", "AnyFixedUnsigned",
    # Functions
    "span", "deref", "take_ptr", "make_default",
    "copy", "copy_iter", "own_iter", "try_parse",
    "assert_send", "assert_sync",
    "CancelledError",
    # I/O
    "BinaryIO", "open_text", "open_binary",
]

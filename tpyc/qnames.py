"""
Qualified name constants for compiler-known types, decorators, and protocols.

These constants are the canonical identifiers used by the parser and sema to
dispatch on well-known names. They match the @builtin_type("...") and
@builtin_decorator("...") annotations in .py stubs under lib/tpy/.
"""

# -- Builtin types --
BOOL = "builtins.bool"
INT = "builtins.int"
FLOAT = "builtins.float"
STR = "builtins.str"
TUPLE = "builtins.tuple"
BASIC_SLICE = "tpy.basic_slice"
SLICE = "builtins.slice"
TYPE = "builtins.type"
NONE = "builtins.None"
LIST = "builtins.list"
DICT = "builtins.dict"
DICT_KEYS = "builtins.dict_keys"
DICT_VALUES = "builtins.dict_values"
DICT_ITEMS = "builtins.dict_items"
SET = "builtins.set"
RANGE = "builtins.Range"
EXCEPTION = "builtins.Exception"
BASE_EXCEPTION = "builtins.BaseException"
STOP_ITERATION = "builtins.StopIteration"

# -- tpy types --
PTR = "tpy.Ptr"
OWN = "tpy.Own"
FN = "tpy.Fn"
SPAN = "tpy.Span"
ARRAY = "tpy.Array"
SPAN_ITER = "tpy.SpanIter"
CHAR = "tpy.Char"
FLOAT32 = "tpy.Float32"
STRING = "tpy.String"
STRVIEW = "tpy.StrView"

# Fixed-width integers
INT8 = "tpy.Int8"
INT16 = "tpy.Int16"
INT32 = "tpy.Int32"
INT64 = "tpy.Int64"
UINT8 = "tpy.UInt8"
UINT16 = "tpy.UInt16"
UINT32 = "tpy.UInt32"
UINT64 = "tpy.UInt64"

# Map from qualified name to short name for all fixed ints
FIXED_INT_NAMES = {
    INT8: "Int8", INT16: "Int16", INT32: "Int32", INT64: "Int64",
    UINT8: "UInt8", UINT16: "UInt16", UINT32: "UInt32", UINT64: "UInt64",
}

# -- tpy decorators / type modifiers --
READONLY = "tpy.readonly"
NOALLOC = "tpy.noalloc"
NOCOPY = "tpy.nocopy"
PURE = "tpy.pure"
INLINE = "tpy.inline"
DYNAMIC = "tpy.dynamic"
ERROR_RETURN = "tpy.error_return"
AUTO_READONLY = "tpy.auto_readonly"
AUTO_OWN = "tpy.auto_own"
UNSAFE_SEND = "tpy.unsafe_send"
UNSAFE_SYNC = "tpy.unsafe_sync"
NOSEND = "tpy.nosend"
NOSYNC = "tpy.nosync"

# -- typing --
PROTOCOL = "typing.Protocol"
SELF = "typing.Self"
OPTIONAL = "typing.Optional"
FINAL = "typing.Final"
CLASSVAR = "typing.ClassVar"
CALLABLE = "typing.Callable"
OVERLOAD = "typing.overload"
OVERRIDE = "typing.override"
LITERAL = "typing.Literal"
TYPED_DICT = "typing.TypedDict"
UNPACK = "typing.Unpack"

# -- typing protocols --
ITERATOR = "typing.Iterator"
ITERABLE = "typing.Iterable"
SIZED = "typing.Sized"
SEQUENCE = "typing.Sequence"

# -- tpy.coro / asyncio --
AWAITABLE = "tpy.coro.Awaitable"
CANCELLABLE = "tpy.coro.Cancellable"
POLL = "tpy.coro.Poll"
ASYNCIO_RUN = "asyncio.run"
ASYNCIO_CREATE_TASK = "asyncio.create_task"

# -- tpy @builtin_function names with bespoke codegen emission --
COPY = "tpy.copy"
COPY_ITER = "tpy.copy_iter"
OWN_ITER = "tpy.own_iter"
TRY_PARSE = "tpy.try_parse"
# Compile-time Send/Sync assertions; checked in sema, elided in codegen.
ASSERT_SEND = "tpy.assert_send"
ASSERT_SYNC = "tpy.assert_sync"

# -- tpy protocols --
NATIVE_ITERABLE = "tpy.NativeIterable"
NATIVE_RANGE_CONSTRUCTIBLE = "tpy.NativeRangeConstructible"
SPANNABLE = "tpy.Spannable"
VALUE_TYPE = "tpy.ValueType"
COPYABLE = "tpy.Copyable"
SEND = "tpy.Send"
SYNC = "tpy.Sync"
DEFAULT = "tpy.Default"
COVARIANT = "tpy.Covariant"
RETURN_EXCEPTION = "tpy.ReturnException"
THROWABLE = "tpy.Throwable"
# Phase 20: methods auto-emitted by codegen on every concrete Throwable
# subclass (clone returns a heap-allocated polymorphic copy at the
# concrete type, __raise__ throws *this, what reads the message field).
# Sema rejects user-defined overrides of these names on Throwable
# subclasses to prevent a C++ redefinition collision.
THROWABLE_ABI_METHODS = ("clone", "__raise__", "what")
HASHABLE = "tpy.Hashable"
COMPARABLE = "tpy.Comparable"
EQUATABLE = "tpy.Equatable"
DEREF = "tpy.Deref"
TRUTHY = "tpy.Truthy"
STRINGABLE = "tpy.Stringable"
REPRESENTABLE = "tpy.Representable"
ANY_FIXED_INT = "tpy.AnyFixedInt"
ANY_FIXED_SIGNED = "tpy.AnyFixedSigned"
ANY_FIXED_UNSIGNED = "tpy.AnyFixedUnsigned"

# -- tpy.extern decorators --
NATIVE = "tpy.extern.native"
EXPORT = "tpy.extern.export"
CPP_TEMPLATE = "tpy.extern.cpp_template"
BUILTIN_TYPE = "tpy.extern.builtin_type"
BUILTIN_DECORATOR = "tpy.extern.builtin_decorator"
VALUE_PTR_COERCION = "tpy.extern.value_ptr_coercion"
NATIVE_PRESERVES_REFS = "tpy.extern.native_preserves_refs"
TYPE_PARAM_DEFAULT = "tpy.extern.type_param_default"
BUILTIN_FUNCTION = "tpy.extern.builtin_function"
DEFAULT_INT = "tpy.extern.DefaultInt"

# -- enum --
ENUM = "enum.Enum"
INT_ENUM = "enum.IntEnum"
ENUM_AUTO = "enum.auto"

# -- builtins special --
STATICMETHOD = "builtins.staticmethod"
PROPERTY = "builtins.property"

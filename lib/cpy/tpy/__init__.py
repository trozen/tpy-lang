"""
TurboPython built-in types and decorators.

Provides Python implementations of TurboPython-specific types (Int32, Ptr, Array, etc.),
allowing TurboPython source files to run in CPython and enabling IDE support.
"""

from __future__ import annotations
from typing import Generic, TypeVar, Callable, Protocol as _Protocol, runtime_checkable as _runtime_checkable
import copy as _copy_module

T = TypeVar('T')


# ---------------------------------------------------------------------------
# Char
# ---------------------------------------------------------------------------

Char = str
String = str
StrView = str


# ---------------------------------------------------------------------------
# Fixed-width integer types
# ---------------------------------------------------------------------------

def _make_fixed_int_type(name: str, bits: int, signed: bool):
    """Factory for fixed-width integer types with overflow wrapping."""
    if signed:
        min_val = -(2 ** (bits - 1))
        max_val = 2 ** (bits - 1) - 1
        mod = 2 ** bits
    else:
        min_val = 0
        max_val = 2 ** bits - 1
        mod = 2 ** bits

    class FixedInt(int):
        MIN = min_val
        MAX = max_val

        def __new__(cls, value: int = 0) -> FixedInt:
            value = int(value)
            if signed:
                if value < min_val or value > max_val:
                    value = ((value - min_val) % mod) + min_val
            else:
                if value < 0 or value > max_val:
                    value = value % mod
            return super().__new__(cls, value)

        # Use FixedInt (not type(self)) so that subclass arithmetic
        # (e.g. IntEnum + Int8) returns the base fixed-int type.
        def __add__(self, other): return FixedInt(int.__add__(self, other))
        def __radd__(self, other): return FixedInt(int.__radd__(self, other))
        def __sub__(self, other): return FixedInt(int.__sub__(self, other))
        def __rsub__(self, other): return FixedInt(int.__rsub__(self, other))
        def __mul__(self, other): return FixedInt(int.__mul__(self, other))
        def __rmul__(self, other): return FixedInt(int.__rmul__(self, other))
        def __floordiv__(self, other): return FixedInt(int.__floordiv__(self, other))
        def __mod__(self, other): return FixedInt(int.__mod__(self, other))
        def __and__(self, other): return FixedInt(int.__and__(self, other))
        def __or__(self, other): return FixedInt(int.__or__(self, other))
        def __xor__(self, other): return FixedInt(int.__xor__(self, other))
        def __lshift__(self, other): return FixedInt(int.__lshift__(self, other))
        def __rshift__(self, other): return FixedInt(int.__rshift__(self, other))
        def __invert__(self): return FixedInt(int.__invert__(self))
        def __repr__(self): return str(int(self))
        def __str__(self): return str(int(self))

    @classmethod
    def trunc(cls, value):
        return cls(int(value))

    FixedInt.trunc = trunc

    if signed:
        def __neg__(self): return FixedInt(int.__neg__(self))
        FixedInt.__neg__ = __neg__

    FixedInt.__name__ = name
    FixedInt.__qualname__ = name
    return FixedInt


Int8 = _make_fixed_int_type("Int8", 8, True)
Int16 = _make_fixed_int_type("Int16", 16, True)
Int32 = _make_fixed_int_type("Int32", 32, True)
Int64 = _make_fixed_int_type("Int64", 64, True)
UInt8 = _make_fixed_int_type("UInt8", 8, False)
UInt16 = _make_fixed_int_type("UInt16", 16, False)
UInt32 = _make_fixed_int_type("UInt32", 32, False)
UInt64 = _make_fixed_int_type("UInt64", 64, False)


# ---------------------------------------------------------------------------
# Float types
# ---------------------------------------------------------------------------

Float32 = float
Float64 = float


# ---------------------------------------------------------------------------
# Pointer types
# ---------------------------------------------------------------------------

class _Ptr(Generic[T]):
    """Mutable pointer simulation."""

    def __init__(self, obj: T):
        self._obj = obj

    def __deref__(self) -> T:
        return self._obj

    def span(self, length):
        """Ptr[T].span(n) -> Span[T] over the first n elements of the storage."""
        n = int(length)
        data = [self._obj._slots[i] for i in range(n)]
        s = _make_span(data)
        s._backing_slots = self._obj._slots
        s._backing_len = n
        return s

    def __getattr__(self, name: str):
        return getattr(self._obj, name)

    def __setattr__(self, name: str, value):
        if name.startswith('_'):
            super().__setattr__(name, value)
        else:
            setattr(self._obj, name, value)

    def __eq__(self, other):
        if isinstance(other, _Ptr):
            return self._obj == other._obj
        return self._obj == other

    def __lt__(self, other):
        if isinstance(other, _Ptr):
            return self._obj < other._obj
        return self._obj < other

    def __le__(self, other):
        if isinstance(other, _Ptr):
            return self._obj <= other._obj
        return self._obj <= other

    def __gt__(self, other):
        if isinstance(other, _Ptr):
            return self._obj > other._obj
        return self._obj > other

    def __ge__(self, other):
        if isinstance(other, _Ptr):
            return self._obj >= other._obj
        return self._obj >= other

    def __hash__(self):
        return hash(self._obj)

    def __deepcopy__(self, memo):
        return _Ptr(self._obj)

    def __copy__(self):
        return _Ptr(self._obj)


class _ConstPtr(Generic[T]):
    """Read-only pointer simulation."""

    def __init__(self, obj: T):
        self._obj = obj

    def __deref__(self) -> T:
        return self._obj

    @property
    def value(self):
        """Dereference to get the underlying value."""
        return self._obj

    def __getattr__(self, name: str):
        return getattr(self._obj, name)

    def __int__(self):
        return int(self._obj)

    def __index__(self):
        return int(self._obj)

    def __deepcopy__(self, memo):
        return _ConstPtr(self._obj)

    def __copy__(self):
        return _ConstPtr(self._obj)


class _PtrMeta(type):
    """Metaclass to support Ptr[T] syntax."""

    def __getitem__(cls, item):
        return cls


class Ptr(metaclass=_PtrMeta):
    """Ptr[T] type - null pointer constructor only."""

    def __new__(cls):
        return _Ptr(None)


def take_ptr(obj):
    """Take a pointer to obj. CPython stub: wraps in _Ptr."""
    return _Ptr(obj)


class _OwnMeta(type):
    """Metaclass to support Own[T] syntax."""

    def __getitem__(cls, item):
        return cls


class Own(metaclass=_OwnMeta):
    """Own[T] - owned/moved object. In CPython, just returns the object."""

    def __new__(cls, obj):
        return obj


# ---------------------------------------------------------------------------
# Container types
# ---------------------------------------------------------------------------

class ArrayMeta(type):
    """Metaclass to support Array[T, N] syntax."""

    def __getitem__(cls, params):
        if not isinstance(params, tuple) or len(params) != 2:
            raise TypeError("Array requires [T, N] syntax")
        elem_type, size = params

        class BoundArray(Array):
            _elem_type = elem_type
            _size = size

        return BoundArray


class Array(metaclass=ArrayMeta):
    """Fixed-size array: Array[T, N] -> std::array<T, N>"""

    _elem_type = None
    _size = 0

    def __init__(self, data: list = None):
        if data is None:
            self._data = [self._elem_type() for _ in range(self._size)]
        else:
            if len(data) != self._size:
                raise ValueError(f"Array size mismatch: expected {self._size}, got {len(data)}")
            self._data = list(data)

    def _normalize_index(self, index: int, msg: str) -> int:
        i = index
        if i < 0:
            i += self._size
        if i < 0 or i >= self._size:
            raise RuntimeError(msg)
        return i

    def unchecked_get(self, index: int):
        return self._data[index]

    def __getitem__(self, index: int):
        i = self._normalize_index(index, f"array index out of bounds")
        return self._data[i]

    def __setitem__(self, index: int, value) -> None:
        i = self._normalize_index(index, f"array index out of bounds")
        self._data[i] = value

    def size(self) -> Int32:
        return Int32(self._size)

    def __len__(self) -> int:
        return self._size

    def __iter__(self):
        return iter(self._data)

    def __repr__(self) -> str:
        return repr(self._data)

    def __str__(self) -> str:
        return repr(self._data)


class SpanMeta(type):
    """Metaclass to support Span[T] syntax."""

    def __getitem__(cls, elem_type):
        class BoundSpan(Span):
            _elem_type = elem_type

        return BoundSpan


class Span(metaclass=SpanMeta):
    """Non-owning mutable view: Span[T] -> std::span<T>"""

    _elem_type = None

    def __init__(self, data):
        if isinstance(data, (list, Array)):
            if hasattr(data, '_data'):
                self._data = data._data
            else:
                self._data = data
        else:
            self._data = list(data)

    def unchecked_get(self, index: int):
        return self._data[index]

    def __getitem__(self, index):
        if isinstance(index, slice):
            return Span(self._data[index])
        i = index
        if i < 0:
            i += len(self._data)
        if i < 0 or i >= len(self._data):
            raise RuntimeError(f"span index out of bounds")
        return self._data[i]

    def __setitem__(self, index: int, value):
        i = index
        if i < 0:
            i += len(self._data)
        if i < 0 or i >= len(self._data):
            raise RuntimeError(f"span index out of bounds")
        self._data[i] = value

    def size(self) -> Int32:
        return Int32(len(self._data))

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self):
        return iter(self._data)

    def __copy__(self):
        new_span = object.__new__(type(self))
        new_span._data = self._data
        return new_span

    def __deepcopy__(self, memo):
        new_span = object.__new__(type(self))
        new_span._data = self._data
        return new_span

    def sort(self):
        self._data.sort()
        if hasattr(self, '_backing_slots'):
            for i in range(self._backing_len):
                self._backing_slots[i] = self._data[i]


def _make_span(data):
    """Helper for _Ptr.span() -- avoids forward reference to Span."""
    return Span(data)


class SpanIter:
    """Iterator over a contiguous span."""

    def __class_getitem__(cls, item):
        return cls

    def __init__(self, span):
        if isinstance(span, Span):
            self._data = span._data
        elif isinstance(span, (list, tuple)):
            self._data = span
        else:
            self._data = list(span)
        self._index = 0

    def __iter__(self):
        return self

    def __next__(self):
        if self._index >= len(self._data):
            raise StopIteration
        val = self._data[self._index]
        self._index += 1
        return val


# ---------------------------------------------------------------------------
# Decorators and modifiers
# ---------------------------------------------------------------------------

def noalloc(func):
    """No-op in CPython. The compiler enforces no-allocation at compile time."""
    return func


def nocopy(cls):
    """No-op in CPython. The compiler enforces no-copy semantics at compile time."""
    return cls


def dynamic(cls):
    """Decorator marking a protocol for dynamic dispatch.

    In CPython simulation, this is a no-op.
    The compiler generates vtable base/adapter classes.
    """
    return cls


class readonly:
    """Decorator and type modifier for readonly references.

    Supports @readonly decorator and readonly[T] subscript syntax.
    No-op in CPython.
    """
    def __class_getitem__(cls, item):
        return item

    def __new__(cls, func_or_flag=None):
        if func_or_flag is None or isinstance(func_or_flag, bool):
            return lambda func: func
        return func_or_flag


def pure(func):
    """No-op in CPython. The compiler tracks purity metadata at compile time."""
    return func


def error_return(exc_type):
    """No-op in CPython -- error_return is a TPy compile-time annotation."""
    def decorator(func):
        return func
    return decorator


class auto_readonly:
    """Decorator and type modifier for @auto_readonly methods.

    Supports @auto_readonly decorator and auto_readonly[T] subscript syntax.
    In CPython, both forms are no-ops: the decorator returns the function unchanged
    and the subscript returns the type argument unchanged.
    """
    def __class_getitem__(cls, item):
        return item

    def __new__(cls, func):
        return func


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------

def copy(obj):
    """Explicit copy for ownership transfer.

    In CPython, uses copy.deepcopy() to match C++ by-value semantics.
    If the object defines __copy__(), delegates to it (matches TurboPython semantics).
    """
    if hasattr(obj, '__copy__'):
        return obj.__copy__()
    return _copy_module.deepcopy(obj)


def try_parse(enum_cls, name: str):
    """Try to parse a string into an enum member. Returns None if not found."""
    for member in enum_cls:
        if member.name == name:
            return member
    return None


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------

@_runtime_checkable
class NativeIterable(_Protocol[T]):
    """Types that support C++ range-based for loops."""
    def __iter__(self): ...


@_runtime_checkable
class Deref(_Protocol[T]):
    """Types that can be dereferenced to yield T."""
    def __deref__(self): ...


class Covariant(_Protocol[T]):
    """Marker: type param T is covariant (safe for G[Child] -> G[Parent])."""
    pass


@_runtime_checkable
class Comparable(_Protocol):
    """Protocol for types supporting comparison with <."""
    def __lt__(self, other: "Comparable") -> bool: ...


@_runtime_checkable
class Equatable(_Protocol):
    """Protocol for types supporting equality with ==."""
    def __eq__(self, other: object) -> bool: ...


@_runtime_checkable
class Truthy(_Protocol):
    """Protocol for types supporting bool() conversion via __bool__."""
    def __bool__(self) -> bool: ...


@_runtime_checkable
class Stringable(_Protocol):
    """Protocol for types supporting str() conversion via __str__."""
    def __str__(self) -> str: ...


@_runtime_checkable
class Representable(_Protocol):
    """Protocol for types supporting repr() conversion via __repr__."""
    def __repr__(self) -> str: ...


class ValueType(_Protocol):
    """Marker for types with value semantics (passed by value, copy on access)."""
    pass


class Default(_Protocol):
    """Marker for types that support default construction."""
    pass


class Send(_Protocol):
    """Marker for types safe to transfer across threads."""
    pass


class Sync(_Protocol):
    """Marker for types safe to share references across threads."""
    pass


class Hashable(_Protocol):
    """Protocol for types supporting hash() via __hash__."""
    def __hash__(self) -> int: ...


@_runtime_checkable
class ReadOnlySpanLike(_Protocol[T]):
    """Protocol for types exposing contiguous storage via __span__() (readonly).

    In CPython, provides __iter__ automatically from __span__().
    In tpyc, used as a protocol constraint for generic/parameter typing.
    """
    def __span__(self):
        raise NotImplementedError

    def __iter__(self):
        return iter(self.__span__())


class _MakeDefault:
    """Callable that constructs a default value of type T.

    Supports both make_default[T]() and make_default() (with type inferred from annotation).
    """
    _type = None

    def __call__(self):
        if self._type is None:
            raise NotImplementedError(
                "make_default() requires an explicit type argument in CPython: make_default[T]()")
        t = self._type
        self._type = None
        return t()

    def __getitem__(self, t):
        bound = _MakeDefault()
        bound._type = t
        return bound


make_default = _MakeDefault()


def span(x):
    """Get a readonly span from a ReadOnlySpanLike type."""
    return x.__span__()

def deref(x):
    """Dereference a Deref[T] type to get the underlying value."""
    return x.__deref__()


Fn = Callable

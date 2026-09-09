"""
TurboPython built-in types and decorators.

Provides Python implementations of TurboPython-specific types (Int32, Ptr, Array, etc.),
allowing TurboPython source files to run in CPython and enabling IDE support.
"""

from __future__ import annotations
from typing import Generic, TypeVar, Callable, Protocol as _Protocol, runtime_checkable as _runtime_checkable
import copy as _copy_module

# Version/implementation identification lives in `tpy.version` submodule
# for API parity with the compiled side (see lib/tpy/tpy/version.py):
#     from tpy.version import __version__, version_info, is_compiled

T = TypeVar('T')


# ---------------------------------------------------------------------------
# Char
# ---------------------------------------------------------------------------

class Char(str):
    """CPython stub: Char(int) -> chr(int), Char(str) -> str."""
    def __new__(cls, val):
        if isinstance(val, int):
            return str.__new__(cls, chr(val))
        return str.__new__(cls, val)
String = str
StrView = str
BytesView = bytes


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

    def _wrap(value):
        return value if value is NotImplemented else FixedInt(value)

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
        # (e.g. IntEnum + Int8) returns the base fixed-int type. _wrap
        # propagates NotImplemented so mixed-type ops (e.g. Int8 + float)
        # fall back to the other operand's reflected dunder rather than
        # crashing on FixedInt(NotImplemented).
        def __add__(self, other): return _wrap(int.__add__(self, other))
        def __radd__(self, other): return _wrap(int.__radd__(self, other))
        def __sub__(self, other): return _wrap(int.__sub__(self, other))
        def __rsub__(self, other): return _wrap(int.__rsub__(self, other))
        def __mul__(self, other): return _wrap(int.__mul__(self, other))
        def __rmul__(self, other): return _wrap(int.__rmul__(self, other))
        def __floordiv__(self, other): return _wrap(int.__floordiv__(self, other))
        def __mod__(self, other): return _wrap(int.__mod__(self, other))
        def __and__(self, other): return _wrap(int.__and__(self, other))
        def __or__(self, other): return _wrap(int.__or__(self, other))
        def __xor__(self, other): return _wrap(int.__xor__(self, other))
        def __lshift__(self, other): return _wrap(int.__lshift__(self, other))
        def __rshift__(self, other): return _wrap(int.__rshift__(self, other))
        def __invert__(self): return FixedInt(int.__invert__(self))
        def __repr__(self): return str(int(self))
        def __str__(self): return str(int(self))

    @classmethod
    def trunc(cls, value):
        return cls(int(value))

    @classmethod
    def add_wrap(cls, a, b):
        return cls(int(a) + int(b))

    @classmethod
    def sub_wrap(cls, a, b):
        return cls(int(a) - int(b))

    @classmethod
    def mul_wrap(cls, a, b):
        return cls(int(a) * int(b))

    @classmethod
    def shl_wrap(cls, a, b):
        return cls(int(a) << int(b))

    @classmethod
    def shr_wrap(cls, a, b):
        # Logical (zero-fill) shift via unsigned route, matching the C++
        # codegen for signed types. For unsigned types this is the same
        # as Python's `>>`; for signed it differs (Python `>>` is
        # arithmetic / sign-extending).
        return cls((int(a) & (mod - 1)) >> int(b))

    FixedInt.trunc = trunc
    FixedInt.add_wrap = add_wrap
    FixedInt.sub_wrap = sub_wrap
    FixedInt.mul_wrap = mul_wrap
    FixedInt.shl_wrap = shl_wrap
    FixedInt.shr_wrap = shr_wrap

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
        if hasattr(self._obj, '_slots'):
            slots = self._obj._slots
            if isinstance(slots, dict):
                data = [slots[i] for i in range(n)]
            else:
                data = [slots[i] for i in range(n)]
            s = _make_span(data)
            s._backing_slots = slots
            s._backing_len = n
            return s
        else:
            return _make_span([self._obj])

    def __getattr__(self, name: str):
        return getattr(self._obj, name)

    def __setattr__(self, name: str, value):
        if name.startswith('_'):
            super().__setattr__(name, value)
        else:
            setattr(self._obj, name, value)

    def __str__(self):
        return str(self._obj)

    def __repr__(self):
        return repr(self._obj)

    def __format__(self, spec):
        return format(self._obj, spec)

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

    def __str__(self):
        return str(self._obj)

    def __repr__(self):
        return repr(self._obj)

    def __format__(self, spec):
        return format(self._obj, spec)

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


def hotpath(func):
    """No-op in CPython. Marks a hot path for the compiler."""
    return func


def inline(func):
    """No-op in CPython. The compiler expands the method in place."""
    return func


def nocopy(cls):
    """No-op in CPython. The compiler enforces no-copy semantics at compile time."""
    return cls


def unsafe_send(target=None, *, if_params_send=False, if_params_sync=False):
    """No-op in CPython. Usable bare (`@unsafe_send`, target is the class) or as a
    factory (`@unsafe_send(if_params_send=True)`, the conditional form)."""
    if target is not None:
        return target
    def deco(cls):
        return cls
    return deco


def unsafe_sync(target=None, *, if_params_send=False, if_params_sync=False):
    """No-op in CPython. Bare or conditional-factory form, like unsafe_send."""
    if target is not None:
        return target
    def deco(cls):
        return cls
    return deco


def nosend(target):
    """No-op in CPython. The compiler forces the Send answer to false."""
    return target


def nosync(target):
    """No-op in CPython. The compiler forces the Sync answer to false."""
    return target


def nomove(target):
    """No-op in CPython. The compiler marks the type non-movable."""
    return target


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


class auto_own:
    """Type modifier for auto_own[Self] / auto_own[T] methods.

    self: auto_own[Self] on a method generates borrowing + consuming overloads.
    auto_own[T] in a return type becomes T in the borrowing clone and Own[T]
    in the consuming clone. In CPython, subscript returns the type unchanged.
    """
    def __class_getitem__(cls, item):
        return item


class unsafe_interior_mutable:
    """Field type modifier marking a field outside the readonly boundary.

    `unsafe_interior_mutable[Ptr[T]]` lets refcount-style bookkeeping be mutated through
    a readonly handle (the std::shared_ptr const-copy pattern). In CPython the
    subscript returns the type unchanged.
    """
    def __class_getitem__(cls, item):
        return item


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------

def copy(obj):
    """Explicit copy for ownership transfer.

    In CPython, uses copy.deepcopy() to match C++ by-value semantics.
    If the object defines __copy__(), delegates to it (matches TurboPython semantics).

    A pointer handed to copy() DIRECTLY stands for the `T&` borrow TPy
    auto-derefs it to at a T-typed slot (`copy(box.get())`), so the copy
    duplicates the POINTEE -- and is checked BEFORE __copy__, whose lookup the
    pointer's attribute forwarding would otherwise answer with the pointer
    protocol's own sharing `__copy__`. A pointer reached inside a deepcopied
    record is a Ptr[T] field instead and shares, which is what
    `_Ptr.__deepcopy__` does.
    """
    if isinstance(obj, (_Ptr, _ConstPtr)):
        target = object.__getattribute__(obj, '_obj')
        if getattr(type(target), '__tpy_heap_slot__', False):
            # The heap slot is the allocation, not a value: copy the payload
            # it holds, so the result is a plain T like TPy's owned copy.
            target = target[0]
        return copy(target)
    if hasattr(obj, '__copy__'):
        return obj.__copy__()
    return _copy_module.deepcopy(obj)

def copy_iter(iterable):
    """Explicit element-by-element copy acknowledgment for iterables.

    In CPython, returns the iterable as-is (CPython uses references, no copy).
    In TurboPython, wraps in CopyIter for element-by-element copy.
    """
    return iterable


def own_iter(iterable):
    """Consuming iteration: moves the container and iterates with moves.

    In CPython, returns the iterable as-is (CPython uses references, no move).
    In TurboPython, moves the container into OwnIter for zero-copy drain.
    """
    return iterable

def try_parse(enum_cls, name: str):
    """Try to parse a string into an enum member. Returns None if not found."""
    for member in enum_cls:
        if member.name == name:
            return member
    return None


class _CompileTimeAssertMeta(type):
    """`assert_send[T]()` / `assert_sync[T]()` are compile-time-only in
    TurboPython; under CPython they are no-ops (no static trait model)."""
    def __getitem__(cls, t):
        return cls

    def __call__(cls):
        return None


class assert_send(metaclass=_CompileTimeAssertMeta): ...

class assert_sync(metaclass=_CompileTimeAssertMeta): ...


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------

@_runtime_checkable
class NativeIterable(_Protocol[T]):
    """Types that support C++ range-based for loops."""
    def __iter__(self): ...


@_runtime_checkable
class Deref(_Protocol[T]):
    """Types that can be dereferenced to yield T.

    Attribute access the wrapper does not define forwards to the payload,
    mirroring the compiler's auto-deref member access (the wrapper's own
    members always win, which is also how `__getattr__` behaves)."""
    def __deref__(self): ...

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self.__deref__(), name)

    def __setattr__(self, name, value):
        # A member WRITE through the wrapper lands on the payload too, unless
        # the wrapper itself declares the name (its own state, e.g. `_ptr`).
        if name.startswith("_") or name in type(self).__dict__ \
                or any(name in c.__dict__ for c in type(self).__mro__[1:]):
            object.__setattr__(self, name, value)
        else:
            setattr(self.__deref__(), name, value)


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


class Copyable(_Protocol):
    """Marker for types whose payload can be copy-constructed."""
    pass


class Default(_Protocol):
    """Marker for types that support default construction."""
    pass


class Send(_Protocol):
    """Marker for types safe to transfer across threads.

    Subscript form Send[T] is the marker wrapper; erases to T in CPython.
    """
    def __class_getitem__(cls, item):
        return item


class Sync(_Protocol):
    """Marker for types safe to share references across threads.

    Subscript form Sync[T] is the marker wrapper; erases to T in CPython.
    """
    def __class_getitem__(cls, item):
        return item


class ReturnException(_Protocol):
    """Marker for return exception types (used with @error_return, not C++ throw)."""
    pass


class Hashable(_Protocol):
    """Protocol for types supporting hash() via __hash__."""
    def __hash__(self) -> int: ...


@_runtime_checkable
class Writable(_Protocol):
    """Text sink: write(str) -> int + flush()."""
    def write(self, text: str) -> int: ...
    def flush(self) -> None: ...


@_runtime_checkable
class Readable(_Protocol):
    """Text source: read() / readline() returning str."""
    def read(self) -> str: ...
    def readline(self) -> str: ...


@_runtime_checkable
class BinaryWritable(_Protocol):
    """Binary sink: write(bytes) -> int + flush()."""
    def write(self, data: bytes) -> int: ...
    def flush(self) -> None: ...


@_runtime_checkable
class BinaryReadable(_Protocol):
    """Binary source: read() / readline() returning bytes."""
    def read(self) -> bytes: ...
    def readline(self) -> bytes: ...


@_runtime_checkable
class Seekable(_Protocol):
    """Seekable stream: seek(pos, whence=0) / tell()."""
    def seek(self, pos: int, whence: int = 0) -> int: ...
    def tell(self) -> int: ...


@_runtime_checkable
class Closable(_Protocol):
    """Closable resource: close()."""
    def close(self) -> None: ...


@_runtime_checkable
class Spannable(_Protocol[T]):
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
    """Get a readonly span from a Spannable type."""
    return x.__span__()

def deref(x):
    """Dereference a Deref[T] type to get the underlying value."""
    return x.__deref__()


Fn = Callable


# ---------------------------------------------------------------------------
# Slice types
# ---------------------------------------------------------------------------

# In TPy, basic_slice and slice are distinct types. In CPython, both map to
# the built-in slice type so isinstance checks work correctly.
basic_slice = slice


# Aliased to CPython's `asyncio.CancelledError` so `raise tpy.
# CancelledError()` and `except asyncio.CancelledError:` resolve to the
# same class under cpy.
import asyncio as _asyncio
CancelledError = _asyncio.CancelledError


class Waker:
    """POD value type. Exec/task_id/generation kept for parity; no-op
    wake() under CPython since there's no executor wired."""
    def __init__(self, exec_=None, task_id: int = 0, generation: int = 0):
        self.exec = exec_
        self.task_id = task_id
        self.generation = generation

    def wake(self) -> None:
        pass


class _PollMeta(type):
    def __getitem__(cls, t):
        return cls


class Poll(metaclass=_PollMeta):
    """Pending or Ready[T] tagged value. Single-use: value() empties the slot."""
    __slots__ = ("_ready", "_value")

    def __init__(self):
        self._ready = False
        self._value = None

    @staticmethod
    def pending() -> "Poll":
        return Poll()

    @staticmethod
    def ready(value) -> "Poll":
        p = Poll()
        p._ready = True
        p._value = value
        return p

    def is_ready(self) -> bool:
        return self._ready

    def is_pending(self) -> bool:
        return not self._ready

    def value(self):
        if not self._ready:
            raise RuntimeError("Poll.value() called on Pending")
        v = self._value
        self._value = None
        self._ready = False
        return v


class _TaskMeta(type):
    """Metaclass for Task[T] subscript syntax (no behavior change in CPython)."""
    def __getitem__(cls, t):
        return cls


class Task(metaclass=_TaskMeta):
    """Type-erased async task (CPython stub: minimal runtime)."""
    __slots__ = ("_awaitable", "_cancelled")

    def __init__(self, awaitable):
        self._awaitable = awaitable
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True
        if self._awaitable is not None:
            setattr(self._awaitable, "__cancel_pending", True)

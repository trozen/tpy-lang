"""
TurboPython built-in types and decorators.

Provides Python implementations of TurboPython-specific types (int32, Ptr, Array, etc.),
allowing TurboPython source files to run in CPython and enabling IDE support.
"""

from __future__ import annotations
from typing import Generic, TypeVar, Callable, Protocol as _Protocol, runtime_checkable as _runtime_checkable
import struct as _struct
import collections.abc as _collections_abc
import os as _os
import sys as _sys
import weakref as _weakref
import typing as _typing

# Version/implementation identification lives in `tpy.version` submodule
# for API parity with the compiled side (see lib/tpy/tpy/version.py):
#     from tpy.version import __version__, version_info, is_compiled

T = TypeVar('T')

# Fast mode (TPY_CPYTHON_FAST=1) makes the fixed-width numbers the plain
# builtins, so a benchmark's CPython/PyPy baseline pays nothing for these
# stubs. Type checkers see that same plain form, whichever mode runs.
_FAST = _os.environ.get("TPY_CPYTHON_FAST") == "1"


# ---------------------------------------------------------------------------
# char
# ---------------------------------------------------------------------------

class char(str):
    """CPython stub: char(int) -> chr(int), char(str) -> str."""
    def __new__(cls, val, /):
        if isinstance(val, int):
            return str.__new__(cls, chr(val))
        return str.__new__(cls, val)
String = str
StrView = str
BytesView = bytes


# ---------------------------------------------------------------------------
# Fixed-width integer types
# ---------------------------------------------------------------------------

def _fixed_widens(a, b):
    """Whether fixed-int class `a` widens into `b`: same sign to more bits,
    or unsigned to a strictly wider signed type -- the compiler's relation."""
    if a._TPY_SIGNED == b._TPY_SIGNED or (not a._TPY_SIGNED and b._TPY_SIGNED):
        return a._TPY_BITS < b._TPY_BITS
    return False


def _make_fixed_int_type(name: str, bits: int, signed: bool):
    """Factory for a fixed-width integer type.

    A value built through the type (`int32(x)`, a field default) is an
    instance of it, so `~`, the shifts, the width helpers and `@dispatch`
    see its width. Arithmetic returns a plain int: an 8/16-bit operation
    computes in the default int in TPy, and a wider one out of range raises
    OverflowError there, so there is no wrapped value to reproduce. The
    constructor raises OverflowError for a value outside the type, as a
    store into a fixed-width slot does; only the `wrap` / `wrap_*` helpers
    and `~` on an unsigned 32/64-bit value wrap."""
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

    def _modular(value):
        value = int(value)
        if signed:
            return ((value - min_val) % mod) + min_val
        return value % mod

    class FixedInt(int):
        MIN = min_val
        MAX = max_val

        def __new__(cls, value: int = 0, /) -> FixedInt:
            # A subclass may add its own __new__ further along the MRO.
            if cls is FixedInt and type(value) is int and min_val <= value <= max_val:
                return int.__new__(cls, value)
            value = int(value)
            if value < min_val or value > max_val:
                raise OverflowError(f"{name} overflow: value out of range")
            return super().__new__(cls, value)

        # A 32/64-bit shift's result has the left operand's type whatever
        # the count's, so `~` over a shifted mask sees its width; an 8/16-bit
        # `<<` and `~` compute in the default int, as their arithmetic does.
        if bits <= 16:
            def __lshift__(self, other): return int.__lshift__(self, other)
            def __invert__(self): return int.__invert__(self)
        else:
            def __lshift__(self, other): return _wrap(int.__lshift__(self, other))
            def __invert__(self): return FixedInt(_modular(int.__invert__(self)))
        def __rshift__(self, other): return _wrap(int.__rshift__(self, other))
        def __repr__(self): return str(int(self))
        def __str__(self): return str(int(self))

    @classmethod
    def wrap(cls, value):
        return cls(_modular(value))

    # The operands are `T` parameters in TPy, so a value outside T raises
    # OverflowError at the argument's conversion there too.
    @classmethod
    def wrap_add(cls, a, b):
        return cls(_modular(int(cls(a)) + int(cls(b))))

    @classmethod
    def wrap_sub(cls, a, b):
        return cls(_modular(int(cls(a)) - int(cls(b))))

    @classmethod
    def wrap_mul(cls, a, b):
        return cls(_modular(int(cls(a)) * int(cls(b))))

    # Reducing the count first keeps a large count from building a huge int.
    @classmethod
    def wrap_shl(cls, a, n):
        return cls(_modular(int(cls(a)) << (int(n) % bits)))

    @classmethod
    def wrap_shr(cls, a, n):
        return cls(_modular(int(cls(a)) >> (int(n) % bits)))

    FixedInt.wrap = wrap
    FixedInt.wrap_add = wrap_add
    FixedInt.wrap_sub = wrap_sub
    FixedInt.wrap_mul = wrap_mul
    FixedInt.wrap_shl = wrap_shl
    FixedInt.wrap_shr = wrap_shr

    FixedInt._TPY_BASE = FixedInt
    FixedInt._TPY_BITS = bits
    FixedInt._TPY_SIGNED = signed
    FixedInt.__name__ = name
    FixedInt.__qualname__ = name
    return FixedInt


# Type checkers only treat a bare TYPE_CHECKING test, and one name per
# assignment, as a type alias.
if _typing.TYPE_CHECKING:
    int8 = int
    int16 = int
    int32 = int
    int64 = int
    uint8 = int
    uint16 = int
    uint32 = int
    uint64 = int
elif _FAST:
    int8 = int16 = int32 = int64 = int
    uint8 = uint16 = uint32 = uint64 = int
else:
    int8 = _make_fixed_int_type("int8", 8, True)
    int16 = _make_fixed_int_type("int16", 16, True)
    int32 = _make_fixed_int_type("int32", 32, True)
    int64 = _make_fixed_int_type("int64", 64, True)
    uint8 = _make_fixed_int_type("uint8", 8, False)
    uint16 = _make_fixed_int_type("uint16", 16, False)
    uint32 = _make_fixed_int_type("uint32", 32, False)
    uint64 = _make_fixed_int_type("uint64", 64, False)


# ---------------------------------------------------------------------------
# Float types
# ---------------------------------------------------------------------------

def _to_f32(value):
    try:
        return _struct.unpack("f", _struct.pack("f", value))[0]
    except OverflowError:
        return float("inf") if value > 0 else float("-inf")


if _typing.TYPE_CHECKING:
    float32 = float
elif _FAST:
    float32 = float
else:
    class float32(float):
        """Single-precision float: every value and every arithmetic result is
        rounded through IEEE binary32, as the C++ `float` is. An operand that
        is a plain `float` is rounded too, so a literal like `f + 0.5` matches
        TPy, where the literal takes float32; a `float`-typed VARIABLE operand
        yields a double in TPy but a float32 here
        (BUGS.md#cpy-float32-double-precision). repr/str stay float's,
        printing the widened double exactly as the C++ runtime does."""

        def __new__(cls, value=0.0, /):
            return super().__new__(cls, _to_f32(float(value)))

    def _f32_wrap(value):
        return value if value is NotImplemented else float32(value)

    for _name in ("add", "radd", "sub", "rsub", "mul", "rmul", "truediv", "rtruediv",
                  "floordiv", "rfloordiv", "mod", "rmod", "pow", "rpow"):
        _dunder = f"__{_name}__"
        setattr(float32, _dunder,
                (lambda op: lambda self, other: _f32_wrap(op(self, other)))(getattr(float, _dunder)))
    for _name in ("neg", "pos", "abs"):
        _dunder = f"__{_name}__"
        setattr(float32, _dunder, (lambda op: lambda self: float32(op(self)))(getattr(float, _dunder)))
    del _name, _dunder

float64 = float


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


# Type checkers see a pointer as its pointee, which TPy reads through
# implicitly (`p.x` on a `Ptr[P]` is `P.x`); the `Any` member admits the null
# pointer (`p = None`), which TPy checks at the dereference instead.
if _typing.TYPE_CHECKING:
    Ptr = _typing.Union[T, _typing.Any]
else:
    class Ptr(metaclass=_PtrMeta):
        """Ptr[T] type - null pointer constructor only."""

        def __new__(cls):
            return _Ptr(None)


def take_ptr(obj):
    """Take a pointer to obj. CPython stub: wraps in _Ptr."""
    return _Ptr(obj)


def _as_annotation(item):
    # A quoted name becomes a ForwardRef, which `| None` accepts and
    # get_type_hints resolves; a bare string would fail at `|`.
    return _typing.ForwardRef(item) if isinstance(item, str) else item


class _OwnMeta(type):
    """Metaclass to support Own[T] syntax."""

    def __getitem__(cls, item):
        # As `readonly[T]`: the wrapped type, so @dispatch matches an
        # `Own[Animal]` parameter as the `Animal` the compiler sees.
        return _as_annotation(item)


if _typing.TYPE_CHECKING:
    Own = _typing.Union[T]
else:
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

    def size(self) -> int32:
        return int32(self._size)

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


# Type checkers see a span as the mutable sequence it views.
if _typing.TYPE_CHECKING:
    Span = _collections_abc.MutableSequence
else:
    class Span(metaclass=SpanMeta):
        """Non-owning mutable view: Span[T] -> std::span<T>"""

        _elem_type = None

        def __init__(self, data, /):
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

        def size(self) -> int32:
            return int32(len(self._data))

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

    def __init__(self, span, /):
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
        return _as_annotation(item)

    def __new__(cls, func_or_flag=None):
        if func_or_flag is None or isinstance(func_or_flag, bool):
            return lambda func: func
        return func_or_flag


def pure(func):
    """No-op in CPython. The compiler tracks purity metadata at compile time."""
    return func


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------

_dispatch_registry: dict[tuple[str, str], list] = {}


def _callable_arity_of_annotation(ann) -> int | None:
    """Parameter count of a `Callable[[T1, ...], R]` / `Fn[...]` annotation,
    or None when the annotation isn't a parameterised callable. Used to
    disambiguate Fn-shape overload variants by the supplied callable's
    arity -- the compiler-side check is arity-aware, so the runtime
    dispatcher must match.
    """
    origin = getattr(ann, "__origin__", None)
    # Python 3.9+ resolves `Callable[X, R].__origin__` to
    # `collections.abc.Callable` regardless of whether the source spelled
    # `typing.Callable` or `collections.abc.Callable`.
    if origin is not _collections_abc.Callable:
        return None
    args_attr = getattr(ann, "__args__", None)
    if not args_attr or len(args_attr) < 1:
        return None
    # Callable[..., R] (open arity) has args = (Ellipsis, R) -- not arity-checkable.
    if args_attr[0] is Ellipsis:
        return None
    return len(args_attr) - 1  # last element is the return type


def _callable_arity_of_value(value) -> int | None:
    """Positional parameter count of a callable value (lambda, def, bound
    method, callable instance), or None when `inspect.signature` can't
    introspect it. Companion of `_callable_arity_of_annotation` for
    arity-based overload dispatch.
    """
    import inspect
    try:
        sig = inspect.signature(value)
    except (TypeError, ValueError):
        return None
    n = 0
    for p in sig.parameters.values():
        if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        n += 1
    return n


def _param_hints(func) -> dict:
    """Resolved annotations, one at a time: a stub module's `from __future__
    import annotations` leaves strings, and one unresolvable annotation
    (a PEP 695 type param, `auto_readonly[T]`) must not discard the plain
    class annotations next to it, or dispatch degrades to arity alone."""
    # A PEP 695 type param is not a module global; resolve it by name.
    type_params = {tp.__name__: tp
                   for tp in getattr(func, "__type_params__", ())}
    try:
        return _typing.get_type_hints(func, localns=type_params)
    except Exception:
        pass
    hints = {}
    globalns = getattr(func, "__globals__", {})
    for name, ann in getattr(func, "__annotations__", {}).items():
        if isinstance(ann, str):
            try:
                ann = eval(ann, globalns, type_params)
            except Exception:
                continue
        if isinstance(ann, _typing.ForwardRef):
            try:
                ann = eval(ann.__forward_arg__, globalns, type_params)
            except Exception:
                pass
        hints[name] = ann
    return hints


def _bind_forward_refs(func, hints: dict, args: tuple) -> dict:
    """A quoted name the module cannot resolve (a nested class) looked up
    on the receiver's class, which exists by the time a method is called."""
    refs = {k: a for k, a in hints.items() if isinstance(a, _typing.ForwardRef)}
    if not refs or not args:
        return hints
    scope = {}
    for cls in reversed(type(args[0]).__mro__):
        scope.update(vars(cls))
    bound = dict(hints)
    for name, ref in refs.items():
        try:
            bound[name] = eval(ref.__forward_arg__,
                               getattr(func, "__globals__", {}), scope)
        except Exception:
            pass
    return bound


def _is_int_at_float(ann, arg) -> bool:
    """An int at a `float` / `float32` slot, which the compiler converts."""
    return (isinstance(arg, int) and not isinstance(arg, bool)
            and isinstance(ann, type) and issubclass(ann, float))


def _keyword_params(func) -> set:
    """The parameter names a keyword argument binds by name. A keyword
    spelling a positional-only parameter's name lands in `**kwargs`, so it
    is never judged or converted at that parameter's annotation."""
    import inspect
    by_name = (inspect.Parameter.POSITIONAL_OR_KEYWORD,
               inspect.Parameter.KEYWORD_ONLY)
    return {name for name, p in inspect.signature(func).parameters.items()
            if p.kind in by_name}


def _float_converted(func, args: tuple, hints: dict,
                     kwargs: dict) -> tuple[tuple, dict]:
    """`args` and `kwargs` with each int at a `float` slot converted, so
    the variant prints `3.0` as the compiled one does -- a keyword as its
    positional twin."""
    import inspect
    params = list(inspect.signature(func).parameters.keys())
    converted = tuple(hints[params[i]](arg)
                      if i < len(params) and params[i] in hints
                      and _is_int_at_float(hints[params[i]], arg) else arg
                      for i, arg in enumerate(args))
    by_name = _keyword_params(func)
    converted_kw = {k: hints[k](v) if k in by_name and k in hints
                    and _is_int_at_float(hints[k], v) else v
                    for k, v in kwargs.items()}
    return converted, converted_kw


def _binds(func, args: tuple, kwargs: dict) -> bool:
    """Whether a call with `args` and `kwargs` binds to `func`'s parameters,
    keywords by name -- the binding the compiler ranks every variant at."""
    import inspect
    try:
        inspect.signature(func).bind(*args, **kwargs)
    except TypeError:
        return False
    return True


def _type_matches(func, args: tuple, hints: dict,
                  kwargs: dict | None = None) -> bool:
    """Check if the args -- positional, and keyword by name -- match the
    function's type annotations."""
    import inspect
    params = list(inspect.signature(func).parameters.keys())
    named = [(params[i], arg) for i, arg in enumerate(args) if i < len(params)]
    by_name = _keyword_params(func)
    named += [(k, v) for k, v in (kwargs or {}).items() if k in by_name]
    for pname, arg in named:
        if pname == "self":
            continue
        if pname not in hints:
            continue
        ann = hints[pname]
        if isinstance(ann, _typing.ForwardRef):
            # A name the stub cannot resolve (a nested class) matches
            # nothing, rather than every argument.
            return False
        if _typing.get_origin(ann) is _typing.Literal:
            # Matched by value, as the compiler resolves a Literal variant;
            # the type check keeps `True` from matching `Literal[1]`.
            if not any(type(arg) is type(v) and arg == v
                       for v in _typing.get_args(ann)):
                return False
            continue
        if isinstance(ann, type):
            # A plain int is not an instance of the fixed-int stubs (they
            # subclass int), but the compiler lets a literal bind to any
            # fixed-int slot it fits; mirror that.
            if (type(arg) is int and issubclass(ann, int) and ann is not int
                    and hasattr(ann, "MIN")):
                if not (ann.MIN <= arg <= ann.MAX):
                    return False
                continue
            # A fixed int also binds a fixed-int slot it widens into, as a
            # compiler argument conversion does.
            arg_base = getattr(type(arg), "_TPY_BASE", None)
            if (arg_base is not None and hasattr(ann, "_TPY_BASE")
                    and _fixed_widens(arg_base, ann)):
                continue
            if _is_int_at_float(ann, arg):
                continue
            if (getattr(ann, "_is_protocol", False)
                    and not getattr(ann, "_is_runtime_protocol", False)):
                # The compiler checks protocol conformance structurally;
                # isinstance raises on a protocol not @runtime_checkable.
                if not all(hasattr(arg, m)
                           for m in getattr(ann, "__protocol_attrs__", ())):
                    return False
                continue
            if not isinstance(arg, ann):
                return False
            continue
        # A builtin container shape (`list[T]`, `dict[K, V]`) accepts only
        # that container; its element types are not checked.
        if _typing.get_origin(ann) in (list, dict, set, frozenset, tuple):
            if not isinstance(arg, _typing.get_origin(ann)):
                return False
            continue
        # Fn[[T1, ...], R] / Callable[[T1, ...], R]: disambiguate by
        # callable arity. Skip when the annotation has open arity
        # (Callable[..., R]) or the arg can't be inspected.
        expected_arity = _callable_arity_of_annotation(ann)
        if expected_arity is not None:
            actual_arity = _callable_arity_of_value(arg)
            if actual_arity is not None and actual_arity != expected_arity:
                return False
        # Other complex annotations (unions, generics): skip; isinstance
        # can't handle them and we'd rather fall through than crash.
    return True


def dispatch(func):
    """CPython implementation of tpy.dispatch.

    The compiler resolves a @dispatch set statically by ranking the
    applicable variants; this stand-in does not rank. The variants are
    accumulated by qualified name and each call runs the FIRST variant in
    declaration order the call binds to (`_binds`: arity and keyword names)
    that `_type_matches` takes, positionals and keywords alike, on the
    annotated parameter types (a class by isinstance, a fixed-int
    slot by the literal's range or a widening, an int at a `float` slot,
    converted to it, a `Literal` by value, a non-runtime protocol by its
    attributes, a builtin container by its class, a `Callable[[...], R]`
    by the callable's arity; element types and unions are not checked).
    When none matches by type, the first the call binds to runs. A test whose
    output CPython also produces therefore declares the compiler's winner
    first; that the compiler's pick does not depend on declaration order
    is pinned by compiler-only cases. Declaring the winner first is not
    enough when two calls to one set have different compiler winners
    this stub cannot tell apart -- a `Literal` value vs a plain variable
    of that value, `@readonly` receiver twins called on a readonly
    receiver, aliased classes (`StrView` / `String` are `str` here),
    element types, union members; such a case goes compiler-only. `@readonly` twins: declare the mutating twin first.
    `typing.overload` is left untouched: its stubs-plus-implementation
    form already runs under stock CPython.
    """
    # Keyed by module too: two modules' same-named sets must not merge.
    key = (func.__module__, func.__qualname__)
    if key not in _dispatch_registry:
        _dispatch_registry[key] = []
    _dispatch_registry[key].append(func)
    variants = _dispatch_registry[key]

    def dispatcher(*args, **kwargs):
        n = len(args) + len(kwargs)
        # Pass 1: the first variant the call binds to whose types match.
        for variant in variants:
            if _binds(variant, args, kwargs):
                hints = _bind_forward_refs(variant, _param_hints(variant), args)
                if _type_matches(variant, args, hints, kwargs):
                    conv_args, conv_kwargs = _float_converted(
                        variant, args, hints, kwargs)
                    return variant(*conv_args, **conv_kwargs)
        # Pass 2: binding only (fallback for complex annotations)
        for variant in variants:
            if _binds(variant, args, kwargs):
                return variant(*args, **kwargs)
        raise TypeError(
            f"No matching overload for {key[1]} with {n} argument(s)")

    # Preserve metadata for introspection
    dispatcher.__name__ = func.__name__
    dispatcher.__qualname__ = func.__qualname__
    dispatcher.__module__ = func.__module__
    dispatcher.__dispatch__ = True
    return dispatcher


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
        return _as_annotation(item)

    def __new__(cls, func):
        return func


class auto_own:
    """Type modifier for auto_own[Self] / auto_own[T] methods.

    self: auto_own[Self] on a method generates borrowing + consuming overloads.
    auto_own[T] in a return type becomes T in the borrowing clone and Own[T]
    in the consuming clone. In CPython, subscript returns the type unchanged.
    """
    def __class_getitem__(cls, item):
        return _as_annotation(item)


class unsafe_interior_mutable:
    """Reserved field type modifier (an inline field that stays mutable
    through a readonly owner; not implemented -- tpyc rejects it). In CPython
    the subscript returns the type unchanged.
    """
    def __class_getitem__(cls, item):
        return _as_annotation(item)


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
    import copy as _copy_module
    return _copy_module.deepcopy(obj)

def copy_iter(iterable):
    """Explicit element-by-element copy acknowledgment for iterables.

    Copies each element through copy() as it is iterated, as TurboPython's
    CopyIter does, so a write through an element never reaches the source
    in either runtime.
    """
    return (copy(x) for x in iterable)


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
    mirroring the compiler's auto-deref member access: the wrapper's own
    members always win. Its own are the names its classes define or
    annotate, plus the fields its `__init__` assigns."""
    def __deref__(self): ...

    def __getattr__(self, name):
        # Python calls this after a wrapper member raised AttributeError too;
        # a declared member's failure is the wrapper's, not the payload's.
        if _is_wrapper_member(type(self), name):
            raise AttributeError(name)
        return getattr(self.__deref__(), name)

    def __setattr__(self, name, value):
        if name in self.__dict__ or _in_wrapper_init(self) \
                or _is_wrapper_member(type(self), name):
            object.__setattr__(self, name, value)
        else:
            setattr(self.__deref__(), name, value)


# Weak keys, so a wrapper class a program drops can still be collected.
_ANNOTATED_FIELDS: _weakref.WeakKeyDictionary = _weakref.WeakKeyDictionary()
_WRAPPER_INITS: _weakref.WeakKeyDictionary = _weakref.WeakKeyDictionary()


def _is_wrapper_member(cls: type, name: str) -> bool:
    if name.startswith("__") and name.endswith("__"):
        return True
    if any(name in c.__dict__ for c in cls.__mro__):
        return True
    # Read last and once: on 3.14 reading annotations runs their expressions,
    # even for their names alone.
    fields = _ANNOTATED_FIELDS.get(cls)
    if fields is None:
        fields = _ANNOTATED_FIELDS[cls] = frozenset(
            n for c in cls.__mro__ for n in _annotation_names(c))
    return name in fields


def _in_wrapper_init(obj) -> bool:
    # `self.f = param` in `__init__` declares a field (the compiler's
    # constructor inference), so the write stays on the wrapper.
    cls = type(obj)
    codes = _WRAPPER_INITS.get(cls)
    if codes is None:
        codes = _WRAPPER_INITS[cls] = frozenset(
            c.__dict__["__init__"].__code__ for c in cls.__mro__
            if hasattr(c.__dict__.get("__init__"), "__code__"))
    frame = _sys._getframe(2)
    code = frame.f_code
    return code in codes and frame.f_locals.get(code.co_varnames[0]) is obj


try:
    import annotationlib as _annotationlib
except ImportError:
    _annotationlib = None


def _annotation_names(c: type):
    if _annotationlib is None:
        return c.__dict__.get("__annotations__", {})
    # STRING keeps an annotation naming a class defined later from raising.
    return _annotationlib.get_annotations(c, format=_annotationlib.Format.STRING)


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


class AnyFixedInt(_Protocol):
    """Marker bound for fixed-width integers; CPython does not enforce bounds."""
    pass


class AnyFixedSigned(AnyFixedInt, _Protocol):
    """Marker bound for signed fixed-width integers."""
    pass


class AnyFixedUnsigned(AnyFixedInt, _Protocol):
    """Marker bound for unsigned fixed-width integers."""
    pass


class ValueType(_Protocol):
    """Marker for types with value semantics (passed by value, copy on access)."""
    pass


class ReferenceType(_Protocol):
    """Marker for types returned as a reference to the object itself."""
    pass


class ComparableRef(Comparable, ReferenceType, _Protocol):
    """A comparable reference type."""
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


# Every CPython exception is a Throwable; the alias keeps
# `isinstance(e, Throwable)` and `Box[Throwable]` meaningful under CPython.
Throwable = BaseException


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
# same class under cpy. Loaded on first use: asyncio pulls in ~90 modules,
# which every later GC walks (PyPy measurably). `__all__` at the end of
# the file lets a star import reach it.
if _typing.TYPE_CHECKING:
    from asyncio import CancelledError as CancelledError
else:
    # Runtime only, so type checkers keep rejecting unknown `tpy.` names.
    def __getattr__(name):
        if name == "CancelledError":
            import asyncio
            globals()[name] = asyncio.CancelledError
            return asyncio.CancelledError
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    def __dir__():
        return sorted({*globals(), "CancelledError"})


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


if not _typing.TYPE_CHECKING:
    # The names a star import took before CancelledError went lazy, plus it.
    __all__ = [n for n in globals() if not n.startswith("_")] + ["CancelledError"]

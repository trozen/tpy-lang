# tpy: cpp_namespace("tpystd::tpy")
from .._typing import Protocol, Self, Iterator, Iterable, Sized
from tpy import (
    Span, Ptr,
    # Primitive types used in method signatures (forward references within this file)
    uint64, int32, float32, int8, int16, int64, uint8, uint16, uint32,
    char, String, StrView,
)
from .._bootstrap._decorators import readonly, pure, nocopy, Own, dynamic, dispatch
from .._bootstrap._extern import native, cpp_template, builtin_type, value_ptr_coercion
from ..mem import UninitStorage as _UninitStorage


# --- Dynamic protocols (vtable-based runtime dispatch) ---

# ABI root for the exception hierarchy. Two virtual methods support
# polymorphic exception storage (Phase 20):
#   clone()    -- heap-allocated polymorphic copy at the concrete type;
#                 paired with Box(e.clone()) for Box[Throwable] storage.
#   __raise__() -- re-raises *self as the dynamic type, used by the
#                 `raise <expr>` desugar (Stage 3) so a stored exception
#                 preserves its concrete subclass through C++ unwinding.
# `@native + @dynamic` together: the abstract base class is hand-written
# in runtime/cpp/include/tpy/throwable.hpp as `::tpy::Throwable`. TPy
# resolves `Throwable` to that C++ name and treats the protocol as
# @dynamic for the polymorphism predicate (is_polymorphic_class_type)
# and inheritance-based conformance. Codegen suppresses concept /
# abstract-base / Adapter emission when both decorators are set.
# `BaseException` and subclasses inherit Throwable directly; the
# TPY_THROWABLE_VIRTUALS macro (in throwable.hpp, applied across
# core.hpp / async.hpp) emits the two overrides on every native
# exception class.
@native("tpy::Throwable")
@dynamic
class Throwable(Protocol):
    @readonly
    def clone(self) -> Own[Throwable]: ...

    @readonly
    def __raise__(self) -> None: ...


# --- Structural protocols (concept generated from method signatures) ---

class Truthy(Protocol):
    @readonly
    def __bool__(self) -> bool: ...


class Stringable(Protocol):
    @readonly
    def __str__(self) -> str: ...


class Representable(Protocol):
    @readonly
    def __repr__(self) -> str: ...


class Hashable(Protocol):
    @readonly
    def __hash__(self) -> uint64: ...


class Comparable(Protocol):
    @readonly
    def __lt__(self, other: Self) -> bool: ...


class Equatable(Protocol):
    @readonly
    def __eq__(self, other: Self) -> bool: ...


class Deref[T](Protocol):
    def __deref__(self) -> T: ...


class Spannable[T](Protocol):
    @readonly
    def __span__(self) -> Span[readonly[T]]: ...


class Writable(Protocol):
    def write(self, text: str) -> int32: ...

    def flush(self) -> None: ...


class Readable(Protocol):
    def read(self, size: int32 = -1) -> str: ...

    def readline(self) -> str: ...


class BinaryWritable(Protocol):
    def write(self, data: bytes) -> int32: ...

    def flush(self) -> None: ...


class BinaryReadable(Protocol):
    def read(self, size: int32 = -1) -> bytes: ...

    def readline(self) -> bytes: ...


class Seekable(Protocol):
    def seek(self, pos: int32, whence: int32 = 0) -> int32: ...

    def tell(self) -> int32: ...


class Closable(Protocol):
    def close(self) -> None: ...
    # TODO: add `@property closed -> bool` once Protocol+@property is exercised
    # elsewhere in the codebase. CPython's IOBase exposes both close() and a
    # readable `closed` flag; we ship close() only for v1 to keep the protocol
    # on the verified path. Concrete implementations (StringIO/BytesIO) carry
    # the property today, so users still get `obj.closed` -- just not
    # through a `Closable`-typed parameter.


# --- Marker protocols (no methods, map to runtime C++ concepts) ---

@native("tpy::NativeIterable")
class NativeIterable[T](Iterable[T], Protocol): ...

@native("tpy::NativeRangeConstructible")
class NativeRangeConstructible[T](Protocol): ...

@native("tpy::ValueType")
class ValueType(Protocol): ...


@native("tpy::Copyable")
class Copyable(Protocol): ...

@native("tpy::Send")
class Send(Protocol): ...

@native("tpy::Sync")
class Sync(Protocol): ...

@native("std::default_initializable")
class Default(Protocol): ...

@native("tpy::ReturnException")
class ReturnException(Protocol): ...

@native("tpy::Covariant")
class Covariant[T](Protocol): ...

# Fixed-width integer constraints (for generic functions over int8..uint64)
@native("tpy::AnyFixedInt")
class AnyFixedInt(Protocol): ...

# Both narrow AnyFixedInt, exactly as the C++ concepts do (`AnyFixedSigned =
# AnyFixedInt<T> && is_signed_v<T>`), so a `[T: AnyFixedSigned]` body may use
# anything an `AnyFixedInt` bound admits.
@native("tpy::AnyFixedSigned")
class AnyFixedSigned(AnyFixedInt, Protocol): ...

@native("tpy::AnyFixedUnsigned")
class AnyFixedUnsigned(AnyFixedInt, Protocol): ...

# `Poll`'s body lives in this file (cpp_namespace `tpystd::tpy`) rather
# than alongside `Awaitable` in `tpy/coro/__init__.py` for codegen-
# ordering reasons: the `Awaitable[T]` concept's body needs `Poll[T]`'s
# full type, but codegen emits record full-defs after concepts within
# the same TU. Keeping the body here gets the full def into
# `_core/_types.hpp`, which `coro.hpp` already includes. The TPy qname
# is `tpy.coro.Poll` -- decoupled from the file's cpp_namespace -- so
# users see the same qname they import from (`from tpy.coro import
# Poll`). The C++ symbol is `::tpystd::tpy::Poll<T>` regardless.
# `Waker` / `Awaker` live in `tpy/coro/__init__.py`. `Task` is
# `@builtin_type("tpy.Task")` decorated in `asyncio._executor`.


@builtin_type("tpy.coro.Poll")
@nocopy
class Poll[T]:
    """Result of polling an Awaitable: Pending or Ready[T].

    Single-use: `value()` consumes the contained value. Construct via
    `Poll[T].ready(value)` / `Poll[T].pending()`, or the convenience
    wrappers in `tpy.coro` (`poll_ready` / `poll_pending` /
    `poll_ready_none`).
    """
    # A single owning slot tracks both the payload and its liveness; the
    # slot's own RAII drop covers cleanup, so Poll needs no __del__ and no
    # separate `_has` flag (which would duplicate the slot's alive-bit). The
    # slot moves correctly element-wise, so a Poll carrying an SSO str result
    # survives being moved up the poll chain.
    _slot: _UninitStorage[T]

    def __init__(self) -> None:
        self._slot = _UninitStorage[T]()

    @staticmethod
    def pending() -> Own["Poll[T]"]:
        return Poll[T]()

    @staticmethod
    def ready(value: Own[T]) -> Own["Poll[T]"]:
        p = Poll[T]()
        p._slot.construct(value)
        return p

    @readonly
    def is_ready(self) -> bool:
        return self._slot.has()

    @readonly
    def is_pending(self) -> bool:
        return not self._slot.has()

    def value(self: Own[Self]) -> Own[T]:
        # `Own[Self]` makes the borrow checker reject a second call --
        # the slot is emptied after the first take().
        return self._slot.take()


# --- Primitive type stubs (methods for builtin types) ---

@builtin_type("tpy.float32")
@native("float")
class float32(Comparable, Equatable):
    # Constructors
    @dispatch
    @cpp_template("0.0f")
    def __init__(self) -> None: ...
    @dispatch
    @cpp_template("{0}")
    def __init__(self, x: float32) -> None: ...
    @dispatch
    @cpp_template("static_cast<float>({0})")
    def __init__(self, x: float) -> None: ...
    @dispatch
    @cpp_template("static_cast<float>({0})")
    def __init__(self, x: int) -> None: ...
    @dispatch
    @cpp_template("static_cast<float>({0})")
    def __init__(self, x: bool) -> None: ...
    @dispatch
    @native("tpy::float32_from_str", function=True)
    def __init__(self, x: str) -> None: ...
    @dispatch
    @cpp_template("static_cast<float>({0})")
    def __init__(self, x: int8) -> None: ...
    @dispatch
    @cpp_template("static_cast<float>({0})")
    def __init__(self, x: int16) -> None: ...
    @dispatch
    @cpp_template("static_cast<float>({0})")
    def __init__(self, x: int32) -> None: ...
    @dispatch
    @cpp_template("static_cast<float>({0})")
    def __init__(self, x: int64) -> None: ...
    @dispatch
    @cpp_template("static_cast<float>({0})")
    def __init__(self, x: uint8) -> None: ...
    @dispatch
    @cpp_template("static_cast<float>({0})")
    def __init__(self, x: uint16) -> None: ...
    @dispatch
    @cpp_template("static_cast<float>({0})")
    def __init__(self, x: uint32) -> None: ...
    @dispatch
    @cpp_template("static_cast<float>({0})")
    def __init__(self, x: uint64) -> None: ...

    # Binary operators: float32 op T -> result
    @dispatch
    @cpp_template("({self}) + ({0})")
    def __add__(self, other: float32) -> float32: ...
    @dispatch
    @cpp_template("static_cast<double>({self}) + ({0})")
    def __add__(self, other: float) -> float: ...
    @dispatch
    @cpp_template("({self}) + static_cast<float>({0})")
    def __add__(self, other: int) -> float32: ...
    @dispatch
    @cpp_template("({self}) + static_cast<float>({0})")
    def __add__(self, other: AnyFixedInt) -> float32: ...

    @dispatch
    @cpp_template("({self}) - ({0})")
    def __sub__(self, other: float32) -> float32: ...
    @dispatch
    @cpp_template("static_cast<double>({self}) - ({0})")
    def __sub__(self, other: float) -> float: ...
    @dispatch
    @cpp_template("({self}) - static_cast<float>({0})")
    def __sub__(self, other: int) -> float32: ...
    @dispatch
    @cpp_template("({self}) - static_cast<float>({0})")
    def __sub__(self, other: AnyFixedInt) -> float32: ...

    @dispatch
    @cpp_template("({self}) * ({0})")
    def __mul__(self, other: float32) -> float32: ...
    @dispatch
    @cpp_template("static_cast<double>({self}) * ({0})")
    def __mul__(self, other: float) -> float: ...
    @dispatch
    @cpp_template("({self}) * static_cast<float>({0})")
    def __mul__(self, other: int) -> float32: ...
    @dispatch
    @cpp_template("({self}) * static_cast<float>({0})")
    def __mul__(self, other: AnyFixedInt) -> float32: ...

    @dispatch
    @cpp_template("::tpy::truediv_f32({self}, {0})")
    def __truediv__(self, other: float32) -> float32: ...
    @dispatch
    @cpp_template("::tpy::truediv(static_cast<double>({self}), {0})")
    def __truediv__(self, other: float) -> float: ...
    @dispatch
    @cpp_template("::tpy::truediv_f32({self}, static_cast<float>({0}))")
    def __truediv__(self, other: int) -> float32: ...
    @dispatch
    @cpp_template("::tpy::truediv_f32({self}, static_cast<float>({0}))")
    def __truediv__(self, other: AnyFixedInt) -> float32: ...

    @dispatch
    @cpp_template("::tpy::floordiv_f32({self}, {0})")
    def __floordiv__(self, other: float32) -> float32: ...
    @dispatch
    @cpp_template("::tpy::floordiv(static_cast<double>({self}), {0})")
    def __floordiv__(self, other: float) -> float: ...
    @dispatch
    @cpp_template("::tpy::floordiv_f32({self}, static_cast<float>({0}))")
    def __floordiv__(self, other: int) -> float32: ...
    @dispatch
    @cpp_template("::tpy::floordiv_f32({self}, static_cast<float>({0}))")
    def __floordiv__(self, other: AnyFixedInt) -> float32: ...

    @dispatch
    @cpp_template("::tpy::fmod_f32({self}, {0})")
    def __mod__(self, other: float32) -> float32: ...
    @dispatch
    @cpp_template("::tpy::fmod(static_cast<double>({self}), {0})")
    def __mod__(self, other: float) -> float: ...
    @dispatch
    @cpp_template("::tpy::fmod_f32({self}, static_cast<float>({0}))")
    def __mod__(self, other: int) -> float32: ...
    @dispatch
    @cpp_template("::tpy::fmod_f32({self}, static_cast<float>({0}))")
    def __mod__(self, other: AnyFixedInt) -> float32: ...

    @dispatch
    # The cast keeps a coerced int literal on the float overload: std::pow(float, int) is double.
    @cpp_template("std::pow({self}, static_cast<float>({0}))")
    def __pow__(self, other: float32) -> float32: ...
    @dispatch
    @cpp_template("std::pow(static_cast<double>({self}), {0})")
    def __pow__(self, other: float) -> float: ...
    @dispatch
    @cpp_template("std::pow({self}, static_cast<float>({0}))")
    def __pow__(self, other: int) -> float32: ...
    @dispatch
    @cpp_template("std::pow({self}, static_cast<float>({0}))")
    def __pow__(self, other: AnyFixedInt) -> float32: ...

    # Unary operators
    @cpp_template("+({self})")
    def __pos__(self) -> float32: ...
    @cpp_template("-({self})")
    def __neg__(self) -> float32: ...

    # Reverse operators: T op float32 -> result
    @dispatch
    @cpp_template("({0}) + ({self})")
    def __radd__(self, other: float32) -> float32: ...
    @dispatch
    @cpp_template("({0}) + static_cast<double>({self})")
    def __radd__(self, other: float) -> float: ...
    @dispatch
    @cpp_template("static_cast<float>({0}) + ({self})")
    def __radd__(self, other: int) -> float32: ...
    @dispatch
    @cpp_template("static_cast<float>({0}) + ({self})")
    def __radd__(self, other: AnyFixedInt) -> float32: ...

    @dispatch
    @cpp_template("({0}) - ({self})")
    def __rsub__(self, other: float32) -> float32: ...
    @dispatch
    @cpp_template("({0}) - static_cast<double>({self})")
    def __rsub__(self, other: float) -> float: ...
    @dispatch
    @cpp_template("static_cast<float>({0}) - ({self})")
    def __rsub__(self, other: int) -> float32: ...
    @dispatch
    @cpp_template("static_cast<float>({0}) - ({self})")
    def __rsub__(self, other: AnyFixedInt) -> float32: ...

    @dispatch
    @cpp_template("({0}) * ({self})")
    def __rmul__(self, other: float32) -> float32: ...
    @dispatch
    @cpp_template("({0}) * static_cast<double>({self})")
    def __rmul__(self, other: float) -> float: ...
    @dispatch
    @cpp_template("static_cast<float>({0}) * ({self})")
    def __rmul__(self, other: int) -> float32: ...
    @dispatch
    @cpp_template("static_cast<float>({0}) * ({self})")
    def __rmul__(self, other: AnyFixedInt) -> float32: ...

    @dispatch
    @cpp_template("::tpy::truediv_f32({0}, {self})")
    def __rtruediv__(self, other: float32) -> float32: ...
    @dispatch
    @cpp_template("::tpy::truediv({0}, static_cast<double>({self}))")
    def __rtruediv__(self, other: float) -> float: ...
    @dispatch
    @cpp_template("::tpy::truediv_f32(static_cast<float>({0}), {self})")
    def __rtruediv__(self, other: int) -> float32: ...
    @dispatch
    @cpp_template("::tpy::truediv_f32(static_cast<float>({0}), {self})")
    def __rtruediv__(self, other: AnyFixedInt) -> float32: ...

    @dispatch
    @cpp_template("::tpy::floordiv_f32({0}, {self})")
    def __rfloordiv__(self, other: float32) -> float32: ...
    @dispatch
    @cpp_template("::tpy::floordiv({0}, static_cast<double>({self}))")
    def __rfloordiv__(self, other: float) -> float: ...
    @dispatch
    @cpp_template("::tpy::floordiv_f32(static_cast<float>({0}), {self})")
    def __rfloordiv__(self, other: int) -> float32: ...
    @dispatch
    @cpp_template("::tpy::floordiv_f32(static_cast<float>({0}), {self})")
    def __rfloordiv__(self, other: AnyFixedInt) -> float32: ...

    @dispatch
    @cpp_template("::tpy::fmod_f32({0}, {self})")
    def __rmod__(self, other: float32) -> float32: ...
    @dispatch
    @cpp_template("::tpy::fmod({0}, static_cast<double>({self}))")
    def __rmod__(self, other: float) -> float: ...
    @dispatch
    @cpp_template("::tpy::fmod_f32(static_cast<float>({0}), {self})")
    def __rmod__(self, other: int) -> float32: ...
    @dispatch
    @cpp_template("::tpy::fmod_f32(static_cast<float>({0}), {self})")
    def __rmod__(self, other: AnyFixedInt) -> float32: ...

    @dispatch
    @cpp_template("std::pow(static_cast<float>({0}), {self})")
    def __rpow__(self, other: float32) -> float32: ...
    @dispatch
    @cpp_template("std::pow({0}, static_cast<double>({self}))")
    def __rpow__(self, other: float) -> float: ...
    @dispatch
    @cpp_template("std::pow(static_cast<float>({0}), {self})")
    def __rpow__(self, other: int) -> float32: ...
    @dispatch
    @cpp_template("std::pow(static_cast<float>({0}), {self})")
    def __rpow__(self, other: AnyFixedInt) -> float32: ...

    # Comparison and hashing
    @cpp_template("::tpy::__hash__({self})")
    @readonly
    @pure
    def __hash__(self) -> uint64: ...

    @cpp_template("({self} != 0.0f)")
    @readonly
    @pure
    def __bool__(self) -> bool: ...

    @cpp_template("{self} == {0}")
    @readonly
    @pure
    def __eq__(self, other: float32) -> bool: ...

    @cpp_template("{self} < {0}")
    @readonly
    @pure
    def __lt__(self, other: float32) -> bool: ...


@builtin_type("tpy.int8")
@native("int8_t")
class int8(Comparable, Equatable, AnyFixedInt, AnyFixedSigned):
    @dispatch
    @cpp_template("0")
    def __init__(self) -> None: ...
    @dispatch
    @cpp_template("{0}")
    def __init__(self, x: int8) -> None: ...
    @dispatch
    @cpp_template("::tpy::int_cast_check<{cpp}>({0})")
    def __init__[T: AnyFixedInt](self, x: T) -> None: ...
    @dispatch
    @cpp_template("({0}).to_fixed_check<{cpp}>()")
    def __init__(self, x: int) -> None: ...
    @dispatch
    @cpp_template("::tpy::from_float_check<{cpp}>({0})")
    def __init__(self, x: float) -> None: ...
    @dispatch
    @cpp_template("::tpy::from_float_check<{cpp}>(static_cast<double>({0}))")
    def __init__(self, x: float32) -> None: ...
    @dispatch
    @cpp_template("::tpy::from_str_check<{cpp}>({0})")
    def __init__(self, x: str) -> None: ...
    @dispatch
    @cpp_template("static_cast<{cpp}>({0})")
    def __init__(self, x: bool) -> None: ...
    @dispatch
    @staticmethod
    @cpp_template("static_cast<int8_t>({0})")
    def trunc[T: AnyFixedInt](x: T) -> int8: ...
    @dispatch
    @staticmethod
    @cpp_template("({0}).to_fixed_trunc<int8_t>()")
    def trunc(x: int) -> int8: ...
    # Wrapping arithmetic (mod 2^8). Signed overflow is UB in C++; we
    # route through unsigned to get defined wrap, then cast back.
    @staticmethod
    @cpp_template("static_cast<int8_t>(static_cast<uint8_t>({0}) + static_cast<uint8_t>({1}))")
    def add_wrap(x: int8, y: int8) -> int8: ...
    @staticmethod
    @cpp_template("static_cast<int8_t>(static_cast<uint8_t>({0}) - static_cast<uint8_t>({1}))")
    def sub_wrap(x: int8, y: int8) -> int8: ...
    @staticmethod
    @cpp_template("static_cast<int8_t>(static_cast<uint8_t>({0}) * static_cast<uint8_t>({1}))")
    def mul_wrap(x: int8, y: int8) -> int8: ...
    # Wrapping shifts via unsigned route. Caller must ensure 0 <= n <
    # bitwidth. Signed shr_wrap is logical (zero-fill), not arithmetic.
    @staticmethod
    @cpp_template("static_cast<int8_t>(static_cast<uint8_t>({0}) << ({1}))")
    def shl_wrap(x: int8, n: int8) -> int8: ...
    @staticmethod
    @cpp_template("static_cast<int8_t>(static_cast<uint8_t>({0}) >> ({1}))")
    def shr_wrap(x: int8, n: int8) -> int8: ...
    @cpp_template("::tpy::BigInt({self})")
    def __int__(self) -> int: ...
    @cpp_template("::tpy::add_check<int8_t>({self}, {0})")
    def __add__(self, other: int8) -> int8: ...
    @cpp_template("::tpy::sub_check<int8_t>({self}, {0})")
    def __sub__(self, other: int8) -> int8: ...
    @cpp_template("::tpy::mul_check<int8_t>({self}, {0})")
    def __mul__(self, other: int8) -> int8: ...
    @cpp_template("::tpy::truediv(static_cast<int64_t>({self}), static_cast<int64_t>({0}))")
    def __truediv__(self, other: int8) -> float: ...
    @cpp_template("::tpy::div_check<int8_t>({self}, {0})")
    def __floordiv__(self, other: int8) -> int8: ...
    @cpp_template("::tpy::mod_check<int8_t>({self}, {0})")
    def __mod__(self, other: int8) -> int8: ...
    @cpp_template("::tpy::pow_check<int8_t>({self}, {0})")
    def __pow__(self, other: int8) -> int8: ...
    @cpp_template("::tpy::lshift_check<int8_t>({self}, {0})")
    def __lshift__(self, other: int8) -> int8: ...
    @cpp_template("::tpy::rshift_check<int8_t>({self}, {0})")
    def __rshift__(self, other: int8) -> int8: ...
    @cpp_template("static_cast<int8_t>({self} & {0})")
    def __and__(self, other: int8) -> int8: ...
    @cpp_template("static_cast<int8_t>({self} | {0})")
    def __or__(self, other: int8) -> int8: ...
    @cpp_template("static_cast<int8_t>({self} ^ {0})")
    def __xor__(self, other: int8) -> int8: ...
    @cpp_template("::tpy::add_check<int8_t>({0}, {self})")
    def __radd__(self, other: int8) -> int8: ...
    @cpp_template("::tpy::sub_check<int8_t>({0}, {self})")
    def __rsub__(self, other: int8) -> int8: ...
    @cpp_template("::tpy::mul_check<int8_t>({0}, {self})")
    def __rmul__(self, other: int8) -> int8: ...
    @cpp_template("::tpy::truediv(static_cast<int64_t>({0}), static_cast<int64_t>({self}))")
    def __rtruediv__(self, other: int8) -> float: ...
    @cpp_template("::tpy::div_check<int8_t>({0}, {self})")
    def __rfloordiv__(self, other: int8) -> int8: ...
    @cpp_template("::tpy::mod_check<int8_t>({0}, {self})")
    def __rmod__(self, other: int8) -> int8: ...
    @cpp_template("::tpy::pow_check<int8_t>({0}, {self})")
    def __rpow__(self, other: int8) -> int8: ...
    @cpp_template("::tpy::lshift_check<int8_t>({0}, {self})")
    def __rlshift__(self, other: int8) -> int8: ...
    @cpp_template("::tpy::rshift_check<int8_t>({0}, {self})")
    def __rrshift__(self, other: int8) -> int8: ...
    @cpp_template("static_cast<int8_t>({0} & {self})")
    def __rand__(self, other: int8) -> int8: ...
    @cpp_template("static_cast<int8_t>({0} | {self})")
    def __ror__(self, other: int8) -> int8: ...
    @cpp_template("static_cast<int8_t>({0} ^ {self})")
    def __rxor__(self, other: int8) -> int8: ...
    @cpp_template("static_cast<int8_t>(~({self}))")
    def __invert__(self) -> int8: ...
    @cpp_template("+{self}")
    def __pos__(self) -> int8: ...
    @cpp_template("::tpy::neg_check<int8_t>({self})")
    def __neg__(self) -> int8: ...
    @cpp_template("({self} != 0)")
    @readonly
    @pure
    def __bool__(self) -> bool: ...
    @cpp_template("{self} == {0}")
    @readonly
    @pure
    def __eq__(self, other: int8) -> bool: ...
    @cpp_template("::tpy::__hash__({self})")
    @readonly
    @pure
    def __hash__(self) -> uint64: ...
    @cpp_template("{self} < {0}")
    @readonly
    @pure
    def __lt__(self, other: int8) -> bool: ...


@builtin_type("tpy.int16")
@native("int16_t")
class int16(Comparable, Equatable, AnyFixedInt, AnyFixedSigned):
    @dispatch
    @cpp_template("0")
    def __init__(self) -> None: ...
    @dispatch
    @cpp_template("{0}")
    def __init__(self, x: int16) -> None: ...
    @dispatch
    @cpp_template("::tpy::int_cast_check<{cpp}>({0})")
    def __init__[T: AnyFixedInt](self, x: T) -> None: ...
    @dispatch
    @cpp_template("({0}).to_fixed_check<{cpp}>()")
    def __init__(self, x: int) -> None: ...
    @dispatch
    @cpp_template("::tpy::from_float_check<{cpp}>({0})")
    def __init__(self, x: float) -> None: ...
    @dispatch
    @cpp_template("::tpy::from_float_check<{cpp}>(static_cast<double>({0}))")
    def __init__(self, x: float32) -> None: ...
    @dispatch
    @cpp_template("::tpy::from_str_check<{cpp}>({0})")
    def __init__(self, x: str) -> None: ...
    @dispatch
    @cpp_template("static_cast<{cpp}>({0})")
    def __init__(self, x: bool) -> None: ...
    @dispatch
    @staticmethod
    @cpp_template("static_cast<int16_t>({0})")
    def trunc[T: AnyFixedInt](x: T) -> int16: ...
    @dispatch
    @staticmethod
    @cpp_template("({0}).to_fixed_trunc<int16_t>()")
    def trunc(x: int) -> int16: ...
    @staticmethod
    @cpp_template("static_cast<int16_t>(static_cast<uint16_t>({0}) + static_cast<uint16_t>({1}))")
    def add_wrap(x: int16, y: int16) -> int16: ...
    @staticmethod
    @cpp_template("static_cast<int16_t>(static_cast<uint16_t>({0}) - static_cast<uint16_t>({1}))")
    def sub_wrap(x: int16, y: int16) -> int16: ...
    @staticmethod
    @cpp_template("static_cast<int16_t>(static_cast<uint16_t>({0}) * static_cast<uint16_t>({1}))")
    def mul_wrap(x: int16, y: int16) -> int16: ...
    @staticmethod
    @cpp_template("static_cast<int16_t>(static_cast<uint16_t>({0}) << ({1}))")
    def shl_wrap(x: int16, n: int16) -> int16: ...
    @staticmethod
    @cpp_template("static_cast<int16_t>(static_cast<uint16_t>({0}) >> ({1}))")
    def shr_wrap(x: int16, n: int16) -> int16: ...
    @cpp_template("::tpy::BigInt({self})")
    def __int__(self) -> int: ...
    @cpp_template("::tpy::add_check<int16_t>({self}, {0})")
    def __add__(self, other: int16) -> int16: ...
    @cpp_template("::tpy::sub_check<int16_t>({self}, {0})")
    def __sub__(self, other: int16) -> int16: ...
    @cpp_template("::tpy::mul_check<int16_t>({self}, {0})")
    def __mul__(self, other: int16) -> int16: ...
    @cpp_template("::tpy::truediv(static_cast<int64_t>({self}), static_cast<int64_t>({0}))")
    def __truediv__(self, other: int16) -> float: ...
    @cpp_template("::tpy::div_check<int16_t>({self}, {0})")
    def __floordiv__(self, other: int16) -> int16: ...
    @cpp_template("::tpy::mod_check<int16_t>({self}, {0})")
    def __mod__(self, other: int16) -> int16: ...
    @cpp_template("::tpy::pow_check<int16_t>({self}, {0})")
    def __pow__(self, other: int16) -> int16: ...
    @cpp_template("::tpy::lshift_check<int16_t>({self}, {0})")
    def __lshift__(self, other: int16) -> int16: ...
    @cpp_template("::tpy::rshift_check<int16_t>({self}, {0})")
    def __rshift__(self, other: int16) -> int16: ...
    @cpp_template("static_cast<int16_t>({self} & {0})")
    def __and__(self, other: int16) -> int16: ...
    @cpp_template("static_cast<int16_t>({self} | {0})")
    def __or__(self, other: int16) -> int16: ...
    @cpp_template("static_cast<int16_t>({self} ^ {0})")
    def __xor__(self, other: int16) -> int16: ...
    @cpp_template("::tpy::add_check<int16_t>({0}, {self})")
    def __radd__(self, other: int16) -> int16: ...
    @cpp_template("::tpy::sub_check<int16_t>({0}, {self})")
    def __rsub__(self, other: int16) -> int16: ...
    @cpp_template("::tpy::mul_check<int16_t>({0}, {self})")
    def __rmul__(self, other: int16) -> int16: ...
    @cpp_template("::tpy::truediv(static_cast<int64_t>({0}), static_cast<int64_t>({self}))")
    def __rtruediv__(self, other: int16) -> float: ...
    @cpp_template("::tpy::div_check<int16_t>({0}, {self})")
    def __rfloordiv__(self, other: int16) -> int16: ...
    @cpp_template("::tpy::mod_check<int16_t>({0}, {self})")
    def __rmod__(self, other: int16) -> int16: ...
    @cpp_template("::tpy::pow_check<int16_t>({0}, {self})")
    def __rpow__(self, other: int16) -> int16: ...
    @cpp_template("::tpy::lshift_check<int16_t>({0}, {self})")
    def __rlshift__(self, other: int16) -> int16: ...
    @cpp_template("::tpy::rshift_check<int16_t>({0}, {self})")
    def __rrshift__(self, other: int16) -> int16: ...
    @cpp_template("static_cast<int16_t>({0} & {self})")
    def __rand__(self, other: int16) -> int16: ...
    @cpp_template("static_cast<int16_t>({0} | {self})")
    def __ror__(self, other: int16) -> int16: ...
    @cpp_template("static_cast<int16_t>({0} ^ {self})")
    def __rxor__(self, other: int16) -> int16: ...
    @cpp_template("static_cast<int16_t>(~({self}))")
    def __invert__(self) -> int16: ...
    @cpp_template("+{self}")
    def __pos__(self) -> int16: ...
    @cpp_template("::tpy::neg_check<int16_t>({self})")
    def __neg__(self) -> int16: ...
    @cpp_template("({self} != 0)")
    @readonly
    @pure
    def __bool__(self) -> bool: ...
    @cpp_template("{self} == {0}")
    @readonly
    @pure
    def __eq__(self, other: int16) -> bool: ...
    @cpp_template("::tpy::__hash__({self})")
    @readonly
    @pure
    def __hash__(self) -> uint64: ...
    @cpp_template("{self} < {0}")
    @readonly
    @pure
    def __lt__(self, other: int16) -> bool: ...


@builtin_type("tpy.int32")
@native("int32_t")
class int32(Comparable, Equatable, AnyFixedInt, AnyFixedSigned):
    @dispatch
    @cpp_template("0")
    def __init__(self) -> None: ...
    @dispatch
    @cpp_template("{0}")
    def __init__(self, x: int32) -> None: ...
    @dispatch
    @cpp_template("::tpy::int_cast_check<{cpp}>({0})")
    def __init__[T: AnyFixedInt](self, x: T) -> None: ...
    @dispatch
    @cpp_template("({0}).to_fixed_check<{cpp}>()")
    def __init__(self, x: int) -> None: ...
    @dispatch
    @cpp_template("::tpy::from_float_check<{cpp}>({0})")
    def __init__(self, x: float) -> None: ...
    @dispatch
    @cpp_template("::tpy::from_float_check<{cpp}>(static_cast<double>({0}))")
    def __init__(self, x: float32) -> None: ...
    @dispatch
    @cpp_template("::tpy::from_str_check<{cpp}>({0})")
    def __init__(self, x: str) -> None: ...
    @dispatch
    @cpp_template("static_cast<{cpp}>({0})")
    def __init__(self, x: bool) -> None: ...
    @dispatch
    @staticmethod
    @cpp_template("static_cast<int32_t>({0})")
    def trunc[T: AnyFixedInt](x: T) -> int32: ...
    @dispatch
    @staticmethod
    @cpp_template("({0}).to_fixed_trunc<int32_t>()")
    def trunc(x: int) -> int32: ...
    @staticmethod
    @cpp_template("static_cast<int32_t>(static_cast<uint32_t>({0}) + static_cast<uint32_t>({1}))")
    def add_wrap(x: int32, y: int32) -> int32: ...
    @staticmethod
    @cpp_template("static_cast<int32_t>(static_cast<uint32_t>({0}) - static_cast<uint32_t>({1}))")
    def sub_wrap(x: int32, y: int32) -> int32: ...
    @staticmethod
    @cpp_template("static_cast<int32_t>(static_cast<uint32_t>({0}) * static_cast<uint32_t>({1}))")
    def mul_wrap(x: int32, y: int32) -> int32: ...
    @staticmethod
    @cpp_template("static_cast<int32_t>(static_cast<uint32_t>({0}) << ({1}))")
    def shl_wrap(x: int32, n: int32) -> int32: ...
    @staticmethod
    @cpp_template("static_cast<int32_t>(static_cast<uint32_t>({0}) >> ({1}))")
    def shr_wrap(x: int32, n: int32) -> int32: ...
    @cpp_template("::tpy::BigInt({self})")
    def __int__(self) -> int: ...
    @cpp_template("::tpy::add_check<int32_t>({self}, {0})")
    def __add__(self, other: int32) -> int32: ...
    @cpp_template("::tpy::sub_check<int32_t>({self}, {0})")
    def __sub__(self, other: int32) -> int32: ...
    @cpp_template("::tpy::mul_check<int32_t>({self}, {0})")
    def __mul__(self, other: int32) -> int32: ...
    @cpp_template("::tpy::truediv(static_cast<int64_t>({self}), static_cast<int64_t>({0}))")
    def __truediv__(self, other: int32) -> float: ...
    @cpp_template("::tpy::div_check<int32_t>({self}, {0})")
    def __floordiv__(self, other: int32) -> int32: ...
    @cpp_template("::tpy::mod_check<int32_t>({self}, {0})")
    def __mod__(self, other: int32) -> int32: ...
    @cpp_template("::tpy::pow_check<int32_t>({self}, {0})")
    def __pow__(self, other: int32) -> int32: ...
    @cpp_template("::tpy::lshift_check<int32_t>({self}, {0})")
    def __lshift__(self, other: int32) -> int32: ...
    @cpp_template("::tpy::rshift_check<int32_t>({self}, {0})")
    def __rshift__(self, other: int32) -> int32: ...
    @cpp_template("static_cast<int32_t>({self} & {0})")
    def __and__(self, other: int32) -> int32: ...
    @cpp_template("static_cast<int32_t>({self} | {0})")
    def __or__(self, other: int32) -> int32: ...
    @cpp_template("static_cast<int32_t>({self} ^ {0})")
    def __xor__(self, other: int32) -> int32: ...
    @cpp_template("::tpy::add_check<int32_t>({0}, {self})")
    def __radd__(self, other: int32) -> int32: ...
    @cpp_template("::tpy::sub_check<int32_t>({0}, {self})")
    def __rsub__(self, other: int32) -> int32: ...
    @cpp_template("::tpy::mul_check<int32_t>({0}, {self})")
    def __rmul__(self, other: int32) -> int32: ...
    @cpp_template("::tpy::truediv(static_cast<int64_t>({0}), static_cast<int64_t>({self}))")
    def __rtruediv__(self, other: int32) -> float: ...
    @cpp_template("::tpy::div_check<int32_t>({0}, {self})")
    def __rfloordiv__(self, other: int32) -> int32: ...
    @cpp_template("::tpy::mod_check<int32_t>({0}, {self})")
    def __rmod__(self, other: int32) -> int32: ...
    @cpp_template("::tpy::pow_check<int32_t>({0}, {self})")
    def __rpow__(self, other: int32) -> int32: ...
    @cpp_template("::tpy::lshift_check<int32_t>({0}, {self})")
    def __rlshift__(self, other: int32) -> int32: ...
    @cpp_template("::tpy::rshift_check<int32_t>({0}, {self})")
    def __rrshift__(self, other: int32) -> int32: ...
    @cpp_template("static_cast<int32_t>({0} & {self})")
    def __rand__(self, other: int32) -> int32: ...
    @cpp_template("static_cast<int32_t>({0} | {self})")
    def __ror__(self, other: int32) -> int32: ...
    @cpp_template("static_cast<int32_t>({0} ^ {self})")
    def __rxor__(self, other: int32) -> int32: ...
    @cpp_template("static_cast<int32_t>(~({self}))")
    def __invert__(self) -> int32: ...
    @cpp_template("+{self}")
    def __pos__(self) -> int32: ...
    @cpp_template("::tpy::neg_check<int32_t>({self})")
    def __neg__(self) -> int32: ...
    @cpp_template("({self} != 0)")
    @readonly
    @pure
    def __bool__(self) -> bool: ...
    @cpp_template("{self} == {0}")
    @readonly
    @pure
    def __eq__(self, other: int32) -> bool: ...
    @cpp_template("::tpy::__hash__({self})")
    @readonly
    @pure
    def __hash__(self) -> uint64: ...
    @cpp_template("{self} < {0}")
    @readonly
    @pure
    def __lt__(self, other: int32) -> bool: ...


@builtin_type("tpy.int64")
@native("int64_t")
class int64(Comparable, Equatable, AnyFixedInt, AnyFixedSigned):
    @dispatch
    @cpp_template("0")
    def __init__(self) -> None: ...
    @dispatch
    @cpp_template("{0}")
    def __init__(self, x: int64) -> None: ...
    @dispatch
    @cpp_template("::tpy::int_cast_check<{cpp}>({0})")
    def __init__[T: AnyFixedInt](self, x: T) -> None: ...
    @dispatch
    @cpp_template("({0}).to_fixed_check<{cpp}>()")
    def __init__(self, x: int) -> None: ...
    @dispatch
    @cpp_template("::tpy::from_float_check<{cpp}>({0})")
    def __init__(self, x: float) -> None: ...
    @dispatch
    @cpp_template("::tpy::from_float_check<{cpp}>(static_cast<double>({0}))")
    def __init__(self, x: float32) -> None: ...
    @dispatch
    @cpp_template("::tpy::from_str_check<{cpp}>({0})")
    def __init__(self, x: str) -> None: ...
    @dispatch
    @cpp_template("static_cast<{cpp}>({0})")
    def __init__(self, x: bool) -> None: ...
    @dispatch
    @staticmethod
    @cpp_template("static_cast<int64_t>({0})")
    def trunc[T: AnyFixedInt](x: T) -> int64: ...
    @dispatch
    @staticmethod
    @cpp_template("({0}).to_fixed_trunc<int64_t>()")
    def trunc(x: int) -> int64: ...
    @staticmethod
    @cpp_template("static_cast<int64_t>(static_cast<uint64_t>({0}) + static_cast<uint64_t>({1}))")
    def add_wrap(x: int64, y: int64) -> int64: ...
    @staticmethod
    @cpp_template("static_cast<int64_t>(static_cast<uint64_t>({0}) - static_cast<uint64_t>({1}))")
    def sub_wrap(x: int64, y: int64) -> int64: ...
    @staticmethod
    @cpp_template("static_cast<int64_t>(static_cast<uint64_t>({0}) * static_cast<uint64_t>({1}))")
    def mul_wrap(x: int64, y: int64) -> int64: ...
    @staticmethod
    @cpp_template("static_cast<int64_t>(static_cast<uint64_t>({0}) << ({1}))")
    def shl_wrap(x: int64, n: int64) -> int64: ...
    @staticmethod
    @cpp_template("static_cast<int64_t>(static_cast<uint64_t>({0}) >> ({1}))")
    def shr_wrap(x: int64, n: int64) -> int64: ...
    @cpp_template("::tpy::BigInt({self})")
    def __int__(self) -> int: ...
    @cpp_template("::tpy::add_check<int64_t>({self}, {0})")
    def __add__(self, other: int64) -> int64: ...
    @cpp_template("::tpy::sub_check<int64_t>({self}, {0})")
    def __sub__(self, other: int64) -> int64: ...
    @cpp_template("::tpy::mul_check<int64_t>({self}, {0})")
    def __mul__(self, other: int64) -> int64: ...
    @cpp_template("::tpy::truediv({self}, {0})")
    def __truediv__(self, other: int64) -> float: ...
    @cpp_template("::tpy::div_check<int64_t>({self}, {0})")
    def __floordiv__(self, other: int64) -> int64: ...
    @cpp_template("::tpy::mod_check<int64_t>({self}, {0})")
    def __mod__(self, other: int64) -> int64: ...
    @cpp_template("::tpy::pow_check<int64_t>({self}, {0})")
    def __pow__(self, other: int64) -> int64: ...
    @cpp_template("::tpy::lshift_check<int64_t>({self}, {0})")
    def __lshift__(self, other: int64) -> int64: ...
    @cpp_template("::tpy::rshift_check<int64_t>({self}, {0})")
    def __rshift__(self, other: int64) -> int64: ...
    @cpp_template("static_cast<int64_t>({self} & {0})")
    def __and__(self, other: int64) -> int64: ...
    @cpp_template("static_cast<int64_t>({self} | {0})")
    def __or__(self, other: int64) -> int64: ...
    @cpp_template("static_cast<int64_t>({self} ^ {0})")
    def __xor__(self, other: int64) -> int64: ...
    @cpp_template("::tpy::add_check<int64_t>({0}, {self})")
    def __radd__(self, other: int64) -> int64: ...
    @cpp_template("::tpy::sub_check<int64_t>({0}, {self})")
    def __rsub__(self, other: int64) -> int64: ...
    @cpp_template("::tpy::mul_check<int64_t>({0}, {self})")
    def __rmul__(self, other: int64) -> int64: ...
    @cpp_template("::tpy::truediv({0}, {self})")
    def __rtruediv__(self, other: int64) -> float: ...
    @cpp_template("::tpy::div_check<int64_t>({0}, {self})")
    def __rfloordiv__(self, other: int64) -> int64: ...
    @cpp_template("::tpy::mod_check<int64_t>({0}, {self})")
    def __rmod__(self, other: int64) -> int64: ...
    @cpp_template("::tpy::pow_check<int64_t>({0}, {self})")
    def __rpow__(self, other: int64) -> int64: ...
    @cpp_template("::tpy::lshift_check<int64_t>({0}, {self})")
    def __rlshift__(self, other: int64) -> int64: ...
    @cpp_template("::tpy::rshift_check<int64_t>({0}, {self})")
    def __rrshift__(self, other: int64) -> int64: ...
    @cpp_template("static_cast<int64_t>({0} & {self})")
    def __rand__(self, other: int64) -> int64: ...
    @cpp_template("static_cast<int64_t>({0} | {self})")
    def __ror__(self, other: int64) -> int64: ...
    @cpp_template("static_cast<int64_t>({0} ^ {self})")
    def __rxor__(self, other: int64) -> int64: ...
    @cpp_template("static_cast<int64_t>(~({self}))")
    def __invert__(self) -> int64: ...
    @cpp_template("+{self}")
    def __pos__(self) -> int64: ...
    @cpp_template("::tpy::neg_check<int64_t>({self})")
    def __neg__(self) -> int64: ...
    @cpp_template("({self} != 0)")
    @readonly
    @pure
    def __bool__(self) -> bool: ...
    @cpp_template("{self} == {0}")
    @readonly
    @pure
    def __eq__(self, other: int64) -> bool: ...
    @cpp_template("::tpy::__hash__({self})")
    @readonly
    @pure
    def __hash__(self) -> uint64: ...
    @cpp_template("{self} < {0}")
    @readonly
    @pure
    def __lt__(self, other: int64) -> bool: ...


@builtin_type("tpy.uint8")
@native("uint8_t")
class uint8(Comparable, Equatable, AnyFixedInt, AnyFixedUnsigned):
    @dispatch
    @cpp_template("0")
    def __init__(self) -> None: ...
    @dispatch
    @cpp_template("{0}")
    def __init__(self, x: uint8) -> None: ...
    @dispatch
    @cpp_template("::tpy::int_cast_check<{cpp}>({0})")
    def __init__[T: AnyFixedInt](self, x: T) -> None: ...
    @dispatch
    @cpp_template("({0}).to_fixed_check<{cpp}>()")
    def __init__(self, x: int) -> None: ...
    @dispatch
    @cpp_template("::tpy::from_float_check<{cpp}>({0})")
    def __init__(self, x: float) -> None: ...
    @dispatch
    @cpp_template("::tpy::from_float_check<{cpp}>(static_cast<double>({0}))")
    def __init__(self, x: float32) -> None: ...
    @dispatch
    @cpp_template("::tpy::from_str_check<{cpp}>({0})")
    def __init__(self, x: str) -> None: ...
    @dispatch
    @cpp_template("static_cast<{cpp}>({0})")
    def __init__(self, x: bool) -> None: ...
    @dispatch
    @staticmethod
    @cpp_template("static_cast<uint8_t>({0})")
    def trunc[T: AnyFixedInt](x: T) -> uint8: ...
    @dispatch
    @staticmethod
    @cpp_template("({0}).to_fixed_trunc<uint8_t>()")
    def trunc(x: int) -> uint8: ...
    @staticmethod
    @cpp_template("static_cast<uint8_t>({0} + {1})")
    def add_wrap(x: uint8, y: uint8) -> uint8: ...
    @staticmethod
    @cpp_template("static_cast<uint8_t>({0} - {1})")
    def sub_wrap(x: uint8, y: uint8) -> uint8: ...
    @staticmethod
    @cpp_template("static_cast<uint8_t>({0} * {1})")
    def mul_wrap(x: uint8, y: uint8) -> uint8: ...
    @staticmethod
    @cpp_template("static_cast<uint8_t>(static_cast<uint8_t>({0}) << ({1}))")
    def shl_wrap(x: uint8, n: uint8) -> uint8: ...
    @staticmethod
    @cpp_template("static_cast<uint8_t>(static_cast<uint8_t>({0}) >> ({1}))")
    def shr_wrap(x: uint8, n: uint8) -> uint8: ...
    @cpp_template("::tpy::BigInt({self})")
    def __int__(self) -> int: ...
    @cpp_template("::tpy::add_check<uint8_t>({self}, {0})")
    def __add__(self, other: uint8) -> uint8: ...
    @cpp_template("::tpy::sub_check<uint8_t>({self}, {0})")
    def __sub__(self, other: uint8) -> uint8: ...
    @cpp_template("::tpy::mul_check<uint8_t>({self}, {0})")
    def __mul__(self, other: uint8) -> uint8: ...
    @cpp_template("::tpy::truediv(static_cast<int64_t>({self}), static_cast<int64_t>({0}))")
    def __truediv__(self, other: uint8) -> float: ...
    @cpp_template("::tpy::div_check<uint8_t>({self}, {0})")
    def __floordiv__(self, other: uint8) -> uint8: ...
    @cpp_template("::tpy::mod_check<uint8_t>({self}, {0})")
    def __mod__(self, other: uint8) -> uint8: ...
    @cpp_template("::tpy::pow_check<uint8_t>({self}, {0})")
    def __pow__(self, other: uint8) -> uint8: ...
    @cpp_template("::tpy::lshift_check<uint8_t>({self}, {0})")
    def __lshift__(self, other: uint8) -> uint8: ...
    @cpp_template("::tpy::rshift_check<uint8_t>({self}, {0})")
    def __rshift__(self, other: uint8) -> uint8: ...
    @cpp_template("static_cast<uint8_t>({self} & {0})")
    def __and__(self, other: uint8) -> uint8: ...
    @cpp_template("static_cast<uint8_t>({self} | {0})")
    def __or__(self, other: uint8) -> uint8: ...
    @cpp_template("static_cast<uint8_t>({self} ^ {0})")
    def __xor__(self, other: uint8) -> uint8: ...
    @cpp_template("::tpy::add_check<uint8_t>({0}, {self})")
    def __radd__(self, other: uint8) -> uint8: ...
    @cpp_template("::tpy::sub_check<uint8_t>({0}, {self})")
    def __rsub__(self, other: uint8) -> uint8: ...
    @cpp_template("::tpy::mul_check<uint8_t>({0}, {self})")
    def __rmul__(self, other: uint8) -> uint8: ...
    @cpp_template("::tpy::truediv(static_cast<int64_t>({0}), static_cast<int64_t>({self}))")
    def __rtruediv__(self, other: uint8) -> float: ...
    @cpp_template("::tpy::div_check<uint8_t>({0}, {self})")
    def __rfloordiv__(self, other: uint8) -> uint8: ...
    @cpp_template("::tpy::mod_check<uint8_t>({0}, {self})")
    def __rmod__(self, other: uint8) -> uint8: ...
    @cpp_template("::tpy::pow_check<uint8_t>({0}, {self})")
    def __rpow__(self, other: uint8) -> uint8: ...
    @cpp_template("::tpy::lshift_check<uint8_t>({0}, {self})")
    def __rlshift__(self, other: uint8) -> uint8: ...
    @cpp_template("::tpy::rshift_check<uint8_t>({0}, {self})")
    def __rrshift__(self, other: uint8) -> uint8: ...
    @cpp_template("static_cast<uint8_t>({0} & {self})")
    def __rand__(self, other: uint8) -> uint8: ...
    @cpp_template("static_cast<uint8_t>({0} | {self})")
    def __ror__(self, other: uint8) -> uint8: ...
    @cpp_template("static_cast<uint8_t>({0} ^ {self})")
    def __rxor__(self, other: uint8) -> uint8: ...
    @cpp_template("static_cast<uint8_t>(~({self}))")
    def __invert__(self) -> uint8: ...
    @cpp_template("+{self}")
    def __pos__(self) -> uint8: ...
    @cpp_template("({self} != 0)")
    @readonly
    @pure
    def __bool__(self) -> bool: ...
    @cpp_template("{self} == {0}")
    @readonly
    @pure
    def __eq__(self, other: uint8) -> bool: ...
    @cpp_template("::tpy::__hash__({self})")
    @readonly
    @pure
    def __hash__(self) -> uint64: ...
    @cpp_template("{self} < {0}")
    @readonly
    @pure
    def __lt__(self, other: uint8) -> bool: ...


@builtin_type("tpy.uint16")
@native("uint16_t")
class uint16(Comparable, Equatable, AnyFixedInt, AnyFixedUnsigned):
    @dispatch
    @cpp_template("0")
    def __init__(self) -> None: ...
    @dispatch
    @cpp_template("{0}")
    def __init__(self, x: uint16) -> None: ...
    @dispatch
    @cpp_template("::tpy::int_cast_check<{cpp}>({0})")
    def __init__[T: AnyFixedInt](self, x: T) -> None: ...
    @dispatch
    @cpp_template("({0}).to_fixed_check<{cpp}>()")
    def __init__(self, x: int) -> None: ...
    @dispatch
    @cpp_template("::tpy::from_float_check<{cpp}>({0})")
    def __init__(self, x: float) -> None: ...
    @dispatch
    @cpp_template("::tpy::from_float_check<{cpp}>(static_cast<double>({0}))")
    def __init__(self, x: float32) -> None: ...
    @dispatch
    @cpp_template("::tpy::from_str_check<{cpp}>({0})")
    def __init__(self, x: str) -> None: ...
    @dispatch
    @cpp_template("static_cast<{cpp}>({0})")
    def __init__(self, x: bool) -> None: ...
    @dispatch
    @staticmethod
    @cpp_template("static_cast<uint16_t>({0})")
    def trunc[T: AnyFixedInt](x: T) -> uint16: ...
    @dispatch
    @staticmethod
    @cpp_template("({0}).to_fixed_trunc<uint16_t>()")
    def trunc(x: int) -> uint16: ...
    @staticmethod
    @cpp_template("static_cast<uint16_t>({0} + {1})")
    def add_wrap(x: uint16, y: uint16) -> uint16: ...
    @staticmethod
    @cpp_template("static_cast<uint16_t>({0} - {1})")
    def sub_wrap(x: uint16, y: uint16) -> uint16: ...
    @staticmethod
    @cpp_template("static_cast<uint16_t>({0} * {1})")
    def mul_wrap(x: uint16, y: uint16) -> uint16: ...
    @staticmethod
    @cpp_template("static_cast<uint16_t>(static_cast<uint16_t>({0}) << ({1}))")
    def shl_wrap(x: uint16, n: uint16) -> uint16: ...
    @staticmethod
    @cpp_template("static_cast<uint16_t>(static_cast<uint16_t>({0}) >> ({1}))")
    def shr_wrap(x: uint16, n: uint16) -> uint16: ...
    @cpp_template("::tpy::BigInt({self})")
    def __int__(self) -> int: ...
    @cpp_template("::tpy::add_check<uint16_t>({self}, {0})")
    def __add__(self, other: uint16) -> uint16: ...
    @cpp_template("::tpy::sub_check<uint16_t>({self}, {0})")
    def __sub__(self, other: uint16) -> uint16: ...
    @cpp_template("::tpy::mul_check<uint16_t>({self}, {0})")
    def __mul__(self, other: uint16) -> uint16: ...
    @cpp_template("::tpy::truediv(static_cast<int64_t>({self}), static_cast<int64_t>({0}))")
    def __truediv__(self, other: uint16) -> float: ...
    @cpp_template("::tpy::div_check<uint16_t>({self}, {0})")
    def __floordiv__(self, other: uint16) -> uint16: ...
    @cpp_template("::tpy::mod_check<uint16_t>({self}, {0})")
    def __mod__(self, other: uint16) -> uint16: ...
    @cpp_template("::tpy::pow_check<uint16_t>({self}, {0})")
    def __pow__(self, other: uint16) -> uint16: ...
    @cpp_template("::tpy::lshift_check<uint16_t>({self}, {0})")
    def __lshift__(self, other: uint16) -> uint16: ...
    @cpp_template("::tpy::rshift_check<uint16_t>({self}, {0})")
    def __rshift__(self, other: uint16) -> uint16: ...
    @cpp_template("static_cast<uint16_t>({self} & {0})")
    def __and__(self, other: uint16) -> uint16: ...
    @cpp_template("static_cast<uint16_t>({self} | {0})")
    def __or__(self, other: uint16) -> uint16: ...
    @cpp_template("static_cast<uint16_t>({self} ^ {0})")
    def __xor__(self, other: uint16) -> uint16: ...
    @cpp_template("::tpy::add_check<uint16_t>({0}, {self})")
    def __radd__(self, other: uint16) -> uint16: ...
    @cpp_template("::tpy::sub_check<uint16_t>({0}, {self})")
    def __rsub__(self, other: uint16) -> uint16: ...
    @cpp_template("::tpy::mul_check<uint16_t>({0}, {self})")
    def __rmul__(self, other: uint16) -> uint16: ...
    @cpp_template("::tpy::truediv(static_cast<int64_t>({0}), static_cast<int64_t>({self}))")
    def __rtruediv__(self, other: uint16) -> float: ...
    @cpp_template("::tpy::div_check<uint16_t>({0}, {self})")
    def __rfloordiv__(self, other: uint16) -> uint16: ...
    @cpp_template("::tpy::mod_check<uint16_t>({0}, {self})")
    def __rmod__(self, other: uint16) -> uint16: ...
    @cpp_template("::tpy::pow_check<uint16_t>({0}, {self})")
    def __rpow__(self, other: uint16) -> uint16: ...
    @cpp_template("::tpy::lshift_check<uint16_t>({0}, {self})")
    def __rlshift__(self, other: uint16) -> uint16: ...
    @cpp_template("::tpy::rshift_check<uint16_t>({0}, {self})")
    def __rrshift__(self, other: uint16) -> uint16: ...
    @cpp_template("static_cast<uint16_t>({0} & {self})")
    def __rand__(self, other: uint16) -> uint16: ...
    @cpp_template("static_cast<uint16_t>({0} | {self})")
    def __ror__(self, other: uint16) -> uint16: ...
    @cpp_template("static_cast<uint16_t>({0} ^ {self})")
    def __rxor__(self, other: uint16) -> uint16: ...
    @cpp_template("static_cast<uint16_t>(~({self}))")
    def __invert__(self) -> uint16: ...
    @cpp_template("+{self}")
    def __pos__(self) -> uint16: ...
    @cpp_template("({self} != 0)")
    @readonly
    @pure
    def __bool__(self) -> bool: ...
    @cpp_template("{self} == {0}")
    @readonly
    @pure
    def __eq__(self, other: uint16) -> bool: ...
    @cpp_template("::tpy::__hash__({self})")
    @readonly
    @pure
    def __hash__(self) -> uint64: ...
    @cpp_template("{self} < {0}")
    @readonly
    @pure
    def __lt__(self, other: uint16) -> bool: ...


@builtin_type("tpy.uint32")
@native("uint32_t")
class uint32(Comparable, Equatable, AnyFixedInt, AnyFixedUnsigned):
    @dispatch
    @cpp_template("0")
    def __init__(self) -> None: ...
    @dispatch
    @cpp_template("{0}")
    def __init__(self, x: uint32) -> None: ...
    @dispatch
    @cpp_template("::tpy::int_cast_check<{cpp}>({0})")
    def __init__[T: AnyFixedInt](self, x: T) -> None: ...
    @dispatch
    @cpp_template("({0}).to_fixed_check<{cpp}>()")
    def __init__(self, x: int) -> None: ...
    @dispatch
    @cpp_template("::tpy::from_float_check<{cpp}>({0})")
    def __init__(self, x: float) -> None: ...
    @dispatch
    @cpp_template("::tpy::from_float_check<{cpp}>(static_cast<double>({0}))")
    def __init__(self, x: float32) -> None: ...
    @dispatch
    @cpp_template("::tpy::from_str_check<{cpp}>({0})")
    def __init__(self, x: str) -> None: ...
    @dispatch
    @cpp_template("static_cast<{cpp}>({0})")
    def __init__(self, x: bool) -> None: ...
    @dispatch
    @staticmethod
    @cpp_template("static_cast<uint32_t>({0})")
    def trunc[T: AnyFixedInt](x: T) -> uint32: ...
    @dispatch
    @staticmethod
    @cpp_template("({0}).to_fixed_trunc<uint32_t>()")
    def trunc(x: int) -> uint32: ...
    # Wrapping arithmetic: mod 2^32. No overflow check (unlike +, -, *).
    @staticmethod
    @cpp_template("static_cast<uint32_t>({0} + {1})")
    def add_wrap(x: uint32, y: uint32) -> uint32: ...
    @staticmethod
    @cpp_template("static_cast<uint32_t>({0} - {1})")
    def sub_wrap(x: uint32, y: uint32) -> uint32: ...
    @staticmethod
    @cpp_template("static_cast<uint32_t>({0} * {1})")
    def mul_wrap(x: uint32, y: uint32) -> uint32: ...
    # Inner LHS cast forces the shift at target unsigned width. Without
    # it, a bare int literal {0} (e.g. shl_wrap(1, 31)) would shift as
    # signed int -- UB for uint32 (1 << 31 overflows signed int) and
    # -Wshift-count-overflow for uint64 (count >= int width).
    @staticmethod
    @cpp_template("static_cast<uint32_t>(static_cast<uint32_t>({0}) << ({1}))")
    def shl_wrap(x: uint32, n: uint32) -> uint32: ...
    @staticmethod
    @cpp_template("static_cast<uint32_t>(static_cast<uint32_t>({0}) >> ({1}))")
    def shr_wrap(x: uint32, n: uint32) -> uint32: ...
    @cpp_template("::tpy::BigInt({self})")
    def __int__(self) -> int: ...
    @cpp_template("::tpy::add_check<uint32_t>({self}, {0})")
    def __add__(self, other: uint32) -> uint32: ...
    @cpp_template("::tpy::sub_check<uint32_t>({self}, {0})")
    def __sub__(self, other: uint32) -> uint32: ...
    @cpp_template("::tpy::mul_check<uint32_t>({self}, {0})")
    def __mul__(self, other: uint32) -> uint32: ...
    @cpp_template("::tpy::truediv(static_cast<int64_t>({self}), static_cast<int64_t>({0}))")
    def __truediv__(self, other: uint32) -> float: ...
    @cpp_template("::tpy::div_check<uint32_t>({self}, {0})")
    def __floordiv__(self, other: uint32) -> uint32: ...
    @cpp_template("::tpy::mod_check<uint32_t>({self}, {0})")
    def __mod__(self, other: uint32) -> uint32: ...
    @cpp_template("::tpy::pow_check<uint32_t>({self}, {0})")
    def __pow__(self, other: uint32) -> uint32: ...
    @cpp_template("::tpy::lshift_check<uint32_t>({self}, {0})")
    def __lshift__(self, other: uint32) -> uint32: ...
    @cpp_template("::tpy::rshift_check<uint32_t>({self}, {0})")
    def __rshift__(self, other: uint32) -> uint32: ...
    @cpp_template("static_cast<uint32_t>({self} & {0})")
    def __and__(self, other: uint32) -> uint32: ...
    @cpp_template("static_cast<uint32_t>({self} | {0})")
    def __or__(self, other: uint32) -> uint32: ...
    @cpp_template("static_cast<uint32_t>({self} ^ {0})")
    def __xor__(self, other: uint32) -> uint32: ...
    @cpp_template("::tpy::add_check<uint32_t>({0}, {self})")
    def __radd__(self, other: uint32) -> uint32: ...
    @cpp_template("::tpy::sub_check<uint32_t>({0}, {self})")
    def __rsub__(self, other: uint32) -> uint32: ...
    @cpp_template("::tpy::mul_check<uint32_t>({0}, {self})")
    def __rmul__(self, other: uint32) -> uint32: ...
    @cpp_template("::tpy::truediv(static_cast<int64_t>({0}), static_cast<int64_t>({self}))")
    def __rtruediv__(self, other: uint32) -> float: ...
    @cpp_template("::tpy::div_check<uint32_t>({0}, {self})")
    def __rfloordiv__(self, other: uint32) -> uint32: ...
    @cpp_template("::tpy::mod_check<uint32_t>({0}, {self})")
    def __rmod__(self, other: uint32) -> uint32: ...
    @cpp_template("::tpy::pow_check<uint32_t>({0}, {self})")
    def __rpow__(self, other: uint32) -> uint32: ...
    @cpp_template("::tpy::lshift_check<uint32_t>({0}, {self})")
    def __rlshift__(self, other: uint32) -> uint32: ...
    @cpp_template("::tpy::rshift_check<uint32_t>({0}, {self})")
    def __rrshift__(self, other: uint32) -> uint32: ...
    @cpp_template("static_cast<uint32_t>({0} & {self})")
    def __rand__(self, other: uint32) -> uint32: ...
    @cpp_template("static_cast<uint32_t>({0} | {self})")
    def __ror__(self, other: uint32) -> uint32: ...
    @cpp_template("static_cast<uint32_t>({0} ^ {self})")
    def __rxor__(self, other: uint32) -> uint32: ...
    @cpp_template("static_cast<uint32_t>(~({self}))")
    def __invert__(self) -> uint32: ...
    @cpp_template("+{self}")
    def __pos__(self) -> uint32: ...
    @cpp_template("({self} != 0)")
    @readonly
    @pure
    def __bool__(self) -> bool: ...
    @cpp_template("{self} == {0}")
    @readonly
    @pure
    def __eq__(self, other: uint32) -> bool: ...
    @cpp_template("::tpy::__hash__({self})")
    @readonly
    @pure
    def __hash__(self) -> uint64: ...
    @cpp_template("{self} < {0}")
    @readonly
    @pure
    def __lt__(self, other: uint32) -> bool: ...


@builtin_type("tpy.uint64")
@native("uint64_t")
class uint64(Comparable, Equatable, AnyFixedInt, AnyFixedUnsigned):
    @dispatch
    @cpp_template("0")
    def __init__(self) -> None: ...
    @dispatch
    @cpp_template("{0}")
    def __init__(self, x: uint64) -> None: ...
    @dispatch
    @cpp_template("::tpy::int_cast_check<{cpp}>({0})")
    def __init__[T: AnyFixedInt](self, x: T) -> None: ...
    @dispatch
    @cpp_template("({0}).to_fixed_check<{cpp}>()")
    def __init__(self, x: int) -> None: ...
    @dispatch
    @cpp_template("::tpy::from_float_check<{cpp}>({0})")
    def __init__(self, x: float) -> None: ...
    @dispatch
    @cpp_template("::tpy::from_float_check<{cpp}>(static_cast<double>({0}))")
    def __init__(self, x: float32) -> None: ...
    @dispatch
    @cpp_template("::tpy::from_str_check<{cpp}>({0})")
    def __init__(self, x: str) -> None: ...
    @dispatch
    @cpp_template("static_cast<{cpp}>({0})")
    def __init__(self, x: bool) -> None: ...
    @dispatch
    @staticmethod
    @cpp_template("static_cast<uint64_t>({0})")
    def trunc[T: AnyFixedInt](x: T) -> uint64: ...
    @dispatch
    @staticmethod
    @cpp_template("({0}).to_fixed_trunc<uint64_t>()")
    def trunc(x: int) -> uint64: ...
    @staticmethod
    @cpp_template("static_cast<uint64_t>({0} + {1})")
    def add_wrap(x: uint64, y: uint64) -> uint64: ...
    @staticmethod
    @cpp_template("static_cast<uint64_t>({0} - {1})")
    def sub_wrap(x: uint64, y: uint64) -> uint64: ...
    @staticmethod
    @cpp_template("static_cast<uint64_t>({0} * {1})")
    def mul_wrap(x: uint64, y: uint64) -> uint64: ...
    @staticmethod
    @cpp_template("static_cast<uint64_t>(static_cast<uint64_t>({0}) << ({1}))")
    def shl_wrap(x: uint64, n: uint64) -> uint64: ...
    @staticmethod
    @cpp_template("static_cast<uint64_t>(static_cast<uint64_t>({0}) >> ({1}))")
    def shr_wrap(x: uint64, n: uint64) -> uint64: ...
    @cpp_template("::tpy::BigInt({self})")
    def __int__(self) -> int: ...
    @cpp_template("::tpy::add_check<uint64_t>({self}, {0})")
    def __add__(self, other: uint64) -> uint64: ...
    @cpp_template("::tpy::sub_check<uint64_t>({self}, {0})")
    def __sub__(self, other: uint64) -> uint64: ...
    @cpp_template("::tpy::mul_check<uint64_t>({self}, {0})")
    def __mul__(self, other: uint64) -> uint64: ...
    @cpp_template("::tpy::truediv({self}, {0})")
    def __truediv__(self, other: uint64) -> float: ...
    @cpp_template("::tpy::div_check<uint64_t>({self}, {0})")
    def __floordiv__(self, other: uint64) -> uint64: ...
    @cpp_template("::tpy::mod_check<uint64_t>({self}, {0})")
    def __mod__(self, other: uint64) -> uint64: ...
    @cpp_template("::tpy::pow_check<uint64_t>({self}, {0})")
    def __pow__(self, other: uint64) -> uint64: ...
    @cpp_template("::tpy::lshift_check<uint64_t>({self}, {0})")
    def __lshift__(self, other: uint64) -> uint64: ...
    @cpp_template("::tpy::rshift_check<uint64_t>({self}, {0})")
    def __rshift__(self, other: uint64) -> uint64: ...
    @cpp_template("static_cast<uint64_t>({self} & {0})")
    def __and__(self, other: uint64) -> uint64: ...
    @cpp_template("static_cast<uint64_t>({self} | {0})")
    def __or__(self, other: uint64) -> uint64: ...
    @cpp_template("static_cast<uint64_t>({self} ^ {0})")
    def __xor__(self, other: uint64) -> uint64: ...
    @cpp_template("::tpy::add_check<uint64_t>({0}, {self})")
    def __radd__(self, other: uint64) -> uint64: ...
    @cpp_template("::tpy::sub_check<uint64_t>({0}, {self})")
    def __rsub__(self, other: uint64) -> uint64: ...
    @cpp_template("::tpy::mul_check<uint64_t>({0}, {self})")
    def __rmul__(self, other: uint64) -> uint64: ...
    @cpp_template("::tpy::truediv({0}, {self})")
    def __rtruediv__(self, other: uint64) -> float: ...
    @cpp_template("::tpy::div_check<uint64_t>({0}, {self})")
    def __rfloordiv__(self, other: uint64) -> uint64: ...
    @cpp_template("::tpy::mod_check<uint64_t>({0}, {self})")
    def __rmod__(self, other: uint64) -> uint64: ...
    @cpp_template("::tpy::pow_check<uint64_t>({0}, {self})")
    def __rpow__(self, other: uint64) -> uint64: ...
    @cpp_template("::tpy::lshift_check<uint64_t>({0}, {self})")
    def __rlshift__(self, other: uint64) -> uint64: ...
    @cpp_template("::tpy::rshift_check<uint64_t>({0}, {self})")
    def __rrshift__(self, other: uint64) -> uint64: ...
    @cpp_template("static_cast<uint64_t>({0} & {self})")
    def __rand__(self, other: uint64) -> uint64: ...
    @cpp_template("static_cast<uint64_t>({0} | {self})")
    def __ror__(self, other: uint64) -> uint64: ...
    @cpp_template("static_cast<uint64_t>({0} ^ {self})")
    def __rxor__(self, other: uint64) -> uint64: ...
    @cpp_template("static_cast<uint64_t>(~({self}))")
    def __invert__(self) -> uint64: ...
    @cpp_template("+{self}")
    def __pos__(self) -> uint64: ...
    @cpp_template("({self} != 0)")
    @readonly
    @pure
    def __bool__(self) -> bool: ...
    @cpp_template("{self} == {0}")
    @readonly
    @pure
    def __eq__(self, other: uint64) -> bool: ...
    @cpp_template("::tpy::__hash__({self})")
    @readonly
    @pure
    def __hash__(self) -> uint64: ...
    @cpp_template("{self} < {0}")
    @readonly
    @pure
    def __lt__(self, other: uint64) -> bool: ...


@builtin_type("tpy.char")
@native("char")
class char(Sized, Equatable):
    @dispatch
    @cpp_template("'\\0'")
    def __init__(self) -> None: ...
    @dispatch
    @cpp_template("static_cast<char>({0})")
    def __init__(self, x: int32) -> None: ...
    @dispatch
    @cpp_template("static_cast<char>(({0}).to_fixed_check<int32_t>())")
    def __init__(self, x: int) -> None: ...
    @dispatch
    @native("tpy::char_from_str", function=True)
    def __init__(self, x: str) -> None: ...
    @dispatch
    @native("tpy::char_from_str", function=True)
    def __init__(self, x: String) -> None: ...
    @dispatch
    @native("tpy::char_from_str", function=True)
    def __init__(self, x: StrView) -> None: ...

    @cpp_template("::tpy::__hash__({self})")
    @readonly
    @pure
    def __hash__(self) -> uint64: ...

    @dispatch
    @cpp_template("::tpy::str_concat(::tpy::char_to_str({self}), ::tpy::char_to_str({0}))")
    @readonly
    def __add__(self, other: char) -> String: ...
    @dispatch
    @cpp_template("::tpy::str_concat(::tpy::char_to_str({self}), {0})")
    @readonly
    def __add__(self, other: str) -> String: ...
    @dispatch
    @cpp_template("::tpy::str_concat(::tpy::char_to_str({self}), {0})")
    @readonly
    def __add__(self, other: String) -> String: ...
    @dispatch
    @cpp_template("::tpy::str_concat(::tpy::char_to_str({self}), {0})")
    @readonly
    def __add__(self, other: StrView) -> String: ...

    @cpp_template("::tpy::str_repeat(::tpy::char_to_str({self}), {0})")
    @readonly
    @pure
    def __mul__(self, n: int32) -> str: ...

    @cpp_template("::tpy::str_repeat(::tpy::char_to_str({self}), {0})")
    @readonly
    @pure
    def __rmul__(self, n: int32) -> str: ...

    @cpp_template("{self} == {0}")
    @readonly
    @pure
    def __eq__(self, other: char) -> bool: ...

    @cpp_template("1")
    @readonly
    @pure
    def __len__(self) -> int32: ...



@builtin_type("tpy.String")
@native("::tpy::String")
class String(NativeIterable[char], Iterable[char], Comparable, Equatable):
    @dispatch
    @cpp_template("::tpy::String()")
    def __init__(self) -> None: ...
    @dispatch
    @cpp_template("::tpy::String({0})")
    def __init__(self, x: str) -> None: ...
    @dispatch
    @cpp_template("::tpy::String({0})")
    def __init__(self, x: String) -> None: ...
    @dispatch
    @cpp_template("::tpy::String({0})")
    def __init__(self, x: StrView) -> None: ...
    @dispatch
    @cpp_template("::tpy::String(::tpy::bool_to_str({0}))")
    def __init__(self, x: bool) -> None: ...
    @dispatch
    @cpp_template("::tpy::String(::tpy::char_to_str({0}))")
    def __init__(self, x: char) -> None: ...
    @dispatch
    @cpp_template("::tpy::fixed_to_str<int8_t>({0})")
    def __init__(self, x: int8) -> None: ...
    @dispatch
    @cpp_template("::tpy::fixed_to_str<int16_t>({0})")
    def __init__(self, x: int16) -> None: ...
    @dispatch
    @cpp_template("::tpy::fixed_to_str<int32_t>({0})")
    def __init__(self, x: int32) -> None: ...
    @dispatch
    @cpp_template("::tpy::fixed_to_str<int64_t>({0})")
    def __init__(self, x: int64) -> None: ...
    @dispatch
    @cpp_template("::tpy::fixed_to_str<uint8_t>({0})")
    def __init__(self, x: uint8) -> None: ...
    @dispatch
    @cpp_template("::tpy::fixed_to_str<uint16_t>({0})")
    def __init__(self, x: uint16) -> None: ...
    @dispatch
    @cpp_template("::tpy::fixed_to_str<uint32_t>({0})")
    def __init__(self, x: uint32) -> None: ...
    @dispatch
    @cpp_template("::tpy::fixed_to_str<uint64_t>({0})")
    def __init__(self, x: uint64) -> None: ...
    @dispatch
    @cpp_template("::tpy::String(({0}).to_string())")
    def __init__(self, x: int) -> None: ...
    @dispatch
    @native("tpy::float_to_str", function=True)
    def __init__(self, x: float) -> None: ...
    @dispatch
    @cpp_template("::tpy::float_to_str(static_cast<double>({0}))")
    def __init__(self, x: float32) -> None: ...

    @cpp_template("::tpy::__iter__({self})")
    @readonly
    @pure
    def __iter__(self) -> Iterator[char]: ...

    @cpp_template("static_cast<int32_t>({self}.size())")
    @readonly
    @pure
    def __len__(self) -> int32: ...

    @dispatch
    @cpp_template("::tpy::__getitem__({self}, {0})")
    @readonly
    @pure
    def __getitem__(self, index: int32) -> char: ...

    @dispatch
    @cpp_template("::tpy::str_slice({self}, {0})")
    @readonly
    @pure
    def __getitem__(self, index: basic_slice) -> StrView: ...

    @dispatch
    @cpp_template("::tpy::str_stepped_slice({self}, {0})")
    @readonly
    @pure
    def __getitem__(self, index: slice) -> str: ...

    @dispatch
    @cpp_template("::tpy::str_concat({self}, {0})")
    def __add__(self, other: str) -> String: ...
    @dispatch
    @cpp_template("::tpy::str_concat({self}, {0})")
    def __add__(self, other: String) -> String: ...
    @dispatch
    @cpp_template("::tpy::str_concat({self}, {0})")
    def __add__(self, other: StrView) -> String: ...
    @dispatch
    @cpp_template("::tpy::str_concat({self}, ::tpy::char_to_str({0}))")
    def __add__(self, other: char) -> String: ...

    @cpp_template("::tpy::str_repeat({self}, {0})")
    @readonly
    @pure
    def __mul__(self, n: int32) -> str: ...

    @cpp_template("::tpy::str_repeat({self}, {0})")
    @readonly
    @pure
    def __rmul__(self, n: int32) -> str: ...

    @dispatch
    @cpp_template("::tpy::str_split_whitespace({self})")
    @readonly
    @pure
    def split(self) -> Own[list[str]]: ...
    @dispatch
    @cpp_template("::tpy::str_split({self}, {0})")
    @readonly
    @pure
    def split(self, sep: str) -> Own[list[str]]: ...
    @dispatch
    @cpp_template("::tpy::str_split({self}, {0}, {1})")
    @readonly
    @pure
    def split(self, sep: str, maxsplit: int32) -> Own[list[str]]: ...

    @cpp_template("::tpy::str_join({self}, {0})")
    @readonly
    @pure
    def join(self, items: Iterable[str]) -> str: ...

    @cpp_template("::tpy::str_strip({self})")
    @readonly
    @pure
    def strip(self) -> StrView: ...

    @cpp_template("::tpy::str_lstrip({self})")
    @readonly
    @pure
    def lstrip(self) -> StrView: ...

    @cpp_template("::tpy::str_rstrip({self})")
    @readonly
    @pure
    def rstrip(self) -> StrView: ...

    @cpp_template("::tpy::str_replace({self}, {0}, {1})")
    @readonly
    @pure
    def replace(self, old: str, new: str) -> str: ...

    @cpp_template("::tpy::str_find({self}, {0})")
    @readonly
    @pure
    def find(self, sub: str) -> int32: ...

    @cpp_template("::tpy::str_rfind({self}, {0})")
    @readonly
    @pure
    def rfind(self, sub: str) -> int32: ...

    @cpp_template("::tpy::str_index({self}, {0})")
    @readonly
    @pure
    def index(self, sub: str) -> int32: ...

    @cpp_template("::tpy::str_startswith({self}, {0})")
    @readonly
    @pure
    def startswith(self, prefix: str) -> bool: ...

    @cpp_template("::tpy::str_endswith({self}, {0})")
    @readonly
    @pure
    def endswith(self, suffix: str) -> bool: ...

    @cpp_template("::tpy::str_upper({self})")
    @readonly
    @pure
    def upper(self) -> str: ...

    @cpp_template("::tpy::str_lower({self})")
    @readonly
    @pure
    def lower(self) -> str: ...

    @cpp_template("::tpy::str_count({self}, {0})")
    @readonly
    @pure
    def count(self, sub: str) -> int32: ...

    @cpp_template("::tpy::str_isdigit({self})")
    @readonly
    @pure
    def isdigit(self) -> bool: ...

    @cpp_template("::tpy::str_isalpha({self})")
    @readonly
    @pure
    def isalpha(self) -> bool: ...

    @cpp_template("::tpy::str_isalnum({self})")
    @readonly
    @pure
    def isalnum(self) -> bool: ...

    @cpp_template("::tpy::str_isspace({self})")
    @readonly
    @pure
    def isspace(self) -> bool: ...

    @cpp_template("::tpy::str_isupper({self})")
    @readonly
    @pure
    def isupper(self) -> bool: ...

    @cpp_template("::tpy::str_islower({self})")
    @readonly
    @pure
    def islower(self) -> bool: ...

    @cpp_template("::tpy::str_capitalize({self})")
    @readonly
    @pure
    def capitalize(self) -> str: ...

    @cpp_template("::tpy::str_title({self})")
    @readonly
    @pure
    def title(self) -> str: ...

    @cpp_template("::tpy::str_swapcase({self})")
    @readonly
    @pure
    def swapcase(self) -> str: ...

    @cpp_template("::tpy::str_removeprefix({self}, {0})")
    @readonly
    @pure
    def removeprefix(self, prefix: str) -> StrView: ...

    @cpp_template("::tpy::str_removesuffix({self}, {0})")
    @readonly
    @pure
    def removesuffix(self, suffix: str) -> StrView: ...

    @cpp_template("::tpy::str_rindex({self}, {0})")
    @readonly
    @pure
    def rindex(self, sub: str) -> int32: ...

    @cpp_template("::tpy::str_splitlines({self})")
    @readonly
    @pure
    def splitlines(self) -> Own[list[str]]: ...

    @cpp_template("(!{self}.empty())")
    @readonly
    @pure
    def __bool__(self) -> bool: ...

    @cpp_template("::tpy::__hash__({self})")
    @readonly
    @pure
    def __hash__(self) -> uint64: ...

    @cpp_template("{self} == {0}")
    @readonly
    @pure
    def __eq__(self, other: String) -> bool: ...

    @cpp_template("{self} < {0}")
    @readonly
    @pure
    def __lt__(self, other: String) -> bool: ...



@builtin_type("tpy.FStr")
class FStr:
    """Compile-time f-string decomposition marker.

    When an f-string is passed to an FStr parameter, the compiler keeps the
    f-string decomposed (format template + individual expressions) instead of
    lowering to std::format. Used with call macros for zero-copy logging.
    """
    pass


@builtin_type("tpy.StrView")
@native("std::string_view")
class StrView(NativeIterable[char], Iterable[char], Comparable, Equatable):
    @dispatch
    @cpp_template("std::string_view()")
    def __init__(self) -> None: ...
    @dispatch
    @cpp_template("{0}")
    def __init__(self, x: str) -> None: ...
    @dispatch
    @cpp_template("std::string_view({0})")
    def __init__(self, x: String) -> None: ...
    @dispatch
    @cpp_template("{0}")
    def __init__(self, x: StrView) -> None: ...

    @cpp_template("::tpy::__iter__({self})")
    @readonly
    @pure
    def __iter__(self) -> Iterator[char]: ...

    @cpp_template("static_cast<int32_t>({self}.size())")
    @readonly
    @pure
    def __len__(self) -> int32: ...

    @dispatch
    @cpp_template("::tpy::__getitem__({self}, {0})")
    @readonly
    @pure
    def __getitem__(self, index: int32) -> char: ...

    @dispatch
    @cpp_template("::tpy::str_slice({self}, {0})")
    @readonly
    @pure
    def __getitem__(self, index: basic_slice) -> StrView: ...

    @dispatch
    @cpp_template("::tpy::str_stepped_slice({self}, {0})")
    @readonly
    @pure
    def __getitem__(self, index: slice) -> str: ...

    @dispatch
    @cpp_template("::tpy::str_concat({self}, {0})")
    def __add__(self, other: str) -> String: ...
    @dispatch
    @cpp_template("::tpy::str_concat({self}, {0})")
    def __add__(self, other: String) -> String: ...
    @dispatch
    @cpp_template("::tpy::str_concat({self}, {0})")
    def __add__(self, other: StrView) -> String: ...
    @dispatch
    @cpp_template("::tpy::str_concat({self}, ::tpy::char_to_str({0}))")
    def __add__(self, other: char) -> String: ...

    @cpp_template("::tpy::str_repeat({self}, {0})")
    @readonly
    @pure
    def __mul__(self, n: int32) -> str: ...

    @cpp_template("::tpy::str_repeat({self}, {0})")
    @readonly
    @pure
    def __rmul__(self, n: int32) -> str: ...

    @dispatch
    @cpp_template("::tpy::str_split_whitespace({self})")
    @readonly
    @pure
    def split(self) -> Own[list[str]]: ...
    @dispatch
    @cpp_template("::tpy::str_split({self}, {0})")
    @readonly
    @pure
    def split(self, sep: str) -> Own[list[str]]: ...
    @dispatch
    @cpp_template("::tpy::str_split({self}, {0}, {1})")
    @readonly
    @pure
    def split(self, sep: str, maxsplit: int32) -> Own[list[str]]: ...

    @cpp_template("::tpy::str_join({self}, {0})")
    @readonly
    @pure
    def join(self, items: Iterable[str]) -> str: ...

    @cpp_template("::tpy::str_strip({self})")
    @readonly
    @pure
    def strip(self) -> StrView: ...

    @cpp_template("::tpy::str_lstrip({self})")
    @readonly
    @pure
    def lstrip(self) -> StrView: ...

    @cpp_template("::tpy::str_rstrip({self})")
    @readonly
    @pure
    def rstrip(self) -> StrView: ...

    @cpp_template("::tpy::str_replace({self}, {0}, {1})")
    @readonly
    @pure
    def replace(self, old: str, new: str) -> str: ...

    @cpp_template("::tpy::str_find({self}, {0})")
    @readonly
    @pure
    def find(self, sub: str) -> int32: ...

    @cpp_template("::tpy::str_rfind({self}, {0})")
    @readonly
    @pure
    def rfind(self, sub: str) -> int32: ...

    @cpp_template("::tpy::str_index({self}, {0})")
    @readonly
    @pure
    def index(self, sub: str) -> int32: ...

    @cpp_template("::tpy::str_startswith({self}, {0})")
    @readonly
    @pure
    def startswith(self, prefix: str) -> bool: ...

    @cpp_template("::tpy::str_endswith({self}, {0})")
    @readonly
    @pure
    def endswith(self, suffix: str) -> bool: ...

    @cpp_template("::tpy::str_upper({self})")
    @readonly
    @pure
    def upper(self) -> str: ...

    @cpp_template("::tpy::str_lower({self})")
    @readonly
    @pure
    def lower(self) -> str: ...

    @cpp_template("::tpy::str_count({self}, {0})")
    @readonly
    @pure
    def count(self, sub: str) -> int32: ...

    @cpp_template("::tpy::str_isdigit({self})")
    @readonly
    @pure
    def isdigit(self) -> bool: ...

    @cpp_template("::tpy::str_isalpha({self})")
    @readonly
    @pure
    def isalpha(self) -> bool: ...

    @cpp_template("::tpy::str_isalnum({self})")
    @readonly
    @pure
    def isalnum(self) -> bool: ...

    @cpp_template("::tpy::str_isspace({self})")
    @readonly
    @pure
    def isspace(self) -> bool: ...

    @cpp_template("::tpy::str_isupper({self})")
    @readonly
    @pure
    def isupper(self) -> bool: ...

    @cpp_template("::tpy::str_islower({self})")
    @readonly
    @pure
    def islower(self) -> bool: ...

    @cpp_template("::tpy::str_capitalize({self})")
    @readonly
    @pure
    def capitalize(self) -> str: ...

    @cpp_template("::tpy::str_title({self})")
    @readonly
    @pure
    def title(self) -> str: ...

    @cpp_template("::tpy::str_swapcase({self})")
    @readonly
    @pure
    def swapcase(self) -> str: ...

    @cpp_template("::tpy::str_removeprefix({self}, {0})")
    @readonly
    @pure
    def removeprefix(self, prefix: str) -> StrView: ...

    @cpp_template("::tpy::str_removesuffix({self}, {0})")
    @readonly
    @pure
    def removesuffix(self, suffix: str) -> StrView: ...

    @cpp_template("::tpy::str_rindex({self}, {0})")
    @readonly
    @pure
    def rindex(self, sub: str) -> int32: ...

    @cpp_template("::tpy::str_splitlines({self})")
    @readonly
    @pure
    def splitlines(self) -> Own[list[str]]: ...

    @cpp_template("(!{self}.empty())")
    @readonly
    @pure
    def __bool__(self) -> bool: ...

    @cpp_template("::tpy::__hash__({self})")
    @readonly
    @pure
    def __hash__(self) -> uint64: ...

    @cpp_template("{self} == {0}")
    @readonly
    @pure
    def __eq__(self, other: StrView) -> bool: ...

    @cpp_template("{self} < {0}")
    @readonly
    @pure
    def __lt__(self, other: StrView) -> bool: ...

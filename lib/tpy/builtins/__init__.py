# tpy: native_module
# tpy: cpp_namespace("tpystd::builtins")
from typing import overload, Sized, Iterator, Iterable
from tpy import (
    Int8, Int16, Int32, Int64, UInt8, UInt16, UInt32, UInt64,
    Char, String, StrView, Float32, Own, readonly, pure,
    AnyFixedInt,
)
from tpy import Hashable, Representable, NativeIterable, Truthy, Comparable, Equatable
from tpy.extern import native, cpp_template, builtin_type
from tpy import error_return

from ._list import list
from ._dict import dict, dict_keys, dict_values, dict_items
from ._set import set


# Python exception hierarchy (maps to ::tpy:: runtime structs in core.hpp)
@native("tpy::BaseException")
class BaseException: ...

@native("tpy::Exception")
class Exception(BaseException): ...

@native("tpy::StopIteration")
class StopIteration(Exception): ...


# Range type and range() function
@builtin_type("builtins.Range")
@native("tpy::Range")
class Range[T](NativeIterable[T], Iterable[T]):
    @native("tpy::__iter__", function=True)
    @readonly
    @pure
    def __iter__(self) -> Iterator[T]: ...


@overload
@cpp_template("::tpy::Range<{T}>({0})")
@readonly
@pure
def range[T: AnyFixedInt](stop: T) -> Range[T]: ...
@overload
@cpp_template("::tpy::Range<{T}>({0}, {1})")
@readonly
@pure
def range[T: AnyFixedInt](start: T, stop: T) -> Range[T]: ...
@overload
@cpp_template("::tpy::Range<{T}>({0}, {1}, {2})")
@readonly
@pure
def range[T: AnyFixedInt](start: T, stop: T, step: T) -> Range[T]: ...

@overload
@cpp_template("::tpy::Range<::tpy::BigInt>({0})")
@readonly
@pure
def range(stop: int) -> Range[int]: ...
@overload
@cpp_template("::tpy::Range<::tpy::BigInt>({0}, {1})")
@readonly
@pure
def range(start: int, stop: int) -> Range[int]: ...
@overload
@cpp_template("::tpy::Range<::tpy::BigInt>({0}, {1}, {2})")
@readonly
@pure
def range(start: int, stop: int, step: int) -> Range[int]: ...


@pure
@readonly
@native("tpy::__len__")
def len(x: Sized) -> Int32: ...


@pure
@readonly
@cpp_template("std::string(::tpy::__repr__({0}))")
def repr(x: Representable) -> str: ...


@pure
@readonly
@native("tpy::__hash__")
def hash(x: Hashable) -> UInt64: ...


@overload
@pure
@readonly
@cpp_template("static_cast<char>({0})")
def chr(i: Int32) -> Char: ...

@overload
@pure
@readonly
@cpp_template("static_cast<char>(({0}).to_fixed_check<int32_t>())")
def chr(i: int) -> Char: ...


@overload
@pure
@readonly
@cpp_template("static_cast<int32_t>(static_cast<unsigned char>({0}))")
def ord(c: Char) -> Int32: ...

@overload
@readonly
@native("tpy::ord_str")
def ord(s: str) -> Int32: ...

@overload
@readonly
@native("tpy::ord_str")
def ord(s: String) -> Int32: ...

@overload
@readonly
@native("tpy::ord_str")
def ord(s: StrView) -> Int32: ...


@overload
@pure
@readonly
@native("std::abs")
def abs(x: Int32) -> Int32: ...

@overload
@pure
@readonly
@native("tpy::BigInt::abs")
def abs(x: int) -> int: ...

@overload
@pure
@readonly
@native("std::fabs")
def abs(x: float) -> float: ...


@overload
@pure
@readonly
@native("std::min")
def min(a: Int32, b: Int32) -> Int32: ...

@overload
@pure
@readonly
@native("tpy::min3")
def min(a: Int32, b: Int32, c: Int32) -> Int32: ...

@overload
@pure
@readonly
@native("std::min")
def min(a: int, b: int) -> int: ...

@overload
@pure
@readonly
@native("tpy::min3")
def min(a: int, b: int, c: int) -> int: ...

@overload
@pure
@readonly
@native("std::fmin")
def min(a: float, b: float) -> float: ...

@overload
@pure
@readonly
@cpp_template("std::fmin(std::fmin({0}, {1}), {2})")
def min(a: float, b: float, c: float) -> float: ...


@overload
@pure
@readonly
@native("std::max")
def max(a: Int32, b: Int32) -> Int32: ...

@overload
@pure
@readonly
@native("tpy::max3")
def max(a: Int32, b: Int32, c: Int32) -> Int32: ...

@overload
@pure
@readonly
@native("std::max")
def max(a: int, b: int) -> int: ...

@overload
@pure
@readonly
@native("tpy::max3")
def max(a: int, b: int, c: int) -> int: ...

@overload
@pure
@readonly
@native("std::fmax")
def max(a: float, b: float) -> float: ...

@overload
@pure
@readonly
@cpp_template("std::fmax(std::fmax({0}, {1}), {2})")
def max(a: float, b: float, c: float) -> float: ...


# pow(x, y) -- checked exponentiation
@overload
@pure
@readonly
@cpp_template("({0}).pow({1})")
def pow(x: int, y: int) -> int: ...

@overload
@pure
@readonly
@native("std::pow")
def pow(x: float, y: float) -> float: ...

@overload
@pure
@readonly
@native("tpy::pow_check<int8_t>")
def pow(x: Int8, y: Int8) -> Int8: ...

@overload
@pure
@readonly
@native("tpy::pow_check<int16_t>")
def pow(x: Int16, y: Int16) -> Int16: ...

@overload
@pure
@readonly
@native("tpy::pow_check<int32_t>")
def pow(x: Int32, y: Int32) -> Int32: ...

@overload
@pure
@readonly
@native("tpy::pow_check<int64_t>")
def pow(x: Int64, y: Int64) -> Int64: ...

@overload
@pure
@readonly
@native("tpy::pow_check<uint8_t>")
def pow(x: UInt8, y: UInt8) -> UInt8: ...

@overload
@pure
@readonly
@native("tpy::pow_check<uint16_t>")
def pow(x: UInt16, y: UInt16) -> UInt16: ...

@overload
@pure
@readonly
@native("tpy::pow_check<uint32_t>")
def pow(x: UInt32, y: UInt32) -> UInt32: ...

@overload
@pure
@readonly
@native("tpy::pow_check<uint64_t>")
def pow(x: UInt64, y: UInt64) -> UInt64: ...


# divmod(a, b) -> tuple[T, T]
@overload
@pure
@readonly
@native("tpy::divmod_bigint")
def divmod(a: int, b: int) -> tuple[int, int]: ...

@overload
@pure
@readonly
@native("tpy::divmod_float")
def divmod(a: float, b: float) -> tuple[float, float]: ...

@overload
@pure
@readonly
@native("tpy::divmod_fixed<int8_t>")
def divmod(a: Int8, b: Int8) -> tuple[Int8, Int8]: ...

@overload
@pure
@readonly
@native("tpy::divmod_fixed<int16_t>")
def divmod(a: Int16, b: Int16) -> tuple[Int16, Int16]: ...

@overload
@pure
@readonly
@native("tpy::divmod_fixed<int32_t>")
def divmod(a: Int32, b: Int32) -> tuple[Int32, Int32]: ...

@overload
@pure
@readonly
@native("tpy::divmod_fixed<int64_t>")
def divmod(a: Int64, b: Int64) -> tuple[Int64, Int64]: ...

@overload
@pure
@readonly
@native("tpy::divmod_fixed<uint8_t>")
def divmod(a: UInt8, b: UInt8) -> tuple[UInt8, UInt8]: ...

@overload
@pure
@readonly
@native("tpy::divmod_fixed<uint16_t>")
def divmod(a: UInt16, b: UInt16) -> tuple[UInt16, UInt16]: ...

@overload
@pure
@readonly
@native("tpy::divmod_fixed<uint32_t>")
def divmod(a: UInt32, b: UInt32) -> tuple[UInt32, UInt32]: ...

@overload
@pure
@readonly
@native("tpy::divmod_fixed<uint64_t>")
def divmod(a: UInt64, b: UInt64) -> tuple[UInt64, UInt64]: ...


@error_return(StopIteration)
@native("tpy::next")
def next[T](it: Iterator[T]) -> T: ...
@builtin_type("builtins.bool")
@native("bool")
class bool(Equatable):
    @overload
    @cpp_template("false")
    def __init__(self) -> None: ...
    @overload
    @cpp_template("{0}")
    def __init__(self, x: bool) -> None: ...
    @overload
    @cpp_template("({0} != 0)")
    def __init__(self, x: Int32) -> None: ...
    @overload
    @cpp_template("({0} != 0)")
    def __init__(self, x: int) -> None: ...
    @overload
    @cpp_template("({0} != 0.0)")
    def __init__(self, x: float) -> None: ...
    @overload
    @cpp_template("({0} != 0.0f)")
    def __init__(self, x: Float32) -> None: ...
    @overload
    @cpp_template("(std::string_view({0}).size() != 0)")
    def __init__(self, x: str) -> None: ...
    @overload
    @cpp_template("::tpy::__bool__({0})")
    @readonly
    @pure
    def __init__(self, x: Truthy) -> None: ...

    @cpp_template("{self} == {0}")
    @readonly
    @pure
    def __eq__(self, other: bool) -> bool: ...

    @cpp_template("::tpy::__hash__({self})")
    @readonly
    @pure
    def __hash__(self) -> UInt64: ...



@builtin_type("builtins.int")
@native("::tpy::BigInt")
class int(Comparable, Equatable):
    @overload
    @cpp_template("::tpy::BigInt()")
    def __init__(self) -> None: ...
    @overload
    @cpp_template("{0}")
    def __init__(self, x: int) -> None: ...
    @overload
    @cpp_template("::tpy::BigInt(static_cast<int64_t>({0}))")
    def __init__(self, x: Int8) -> None: ...
    @overload
    @cpp_template("::tpy::BigInt(static_cast<int64_t>({0}))")
    def __init__(self, x: Int16) -> None: ...
    @overload
    @cpp_template("::tpy::BigInt(static_cast<int64_t>({0}))")
    def __init__(self, x: Int32) -> None: ...
    @overload
    @cpp_template("::tpy::BigInt(static_cast<int64_t>({0}))")
    def __init__(self, x: Int64) -> None: ...
    @overload
    @cpp_template("::tpy::BigInt(static_cast<uint64_t>({0}))")
    def __init__(self, x: UInt8) -> None: ...
    @overload
    @cpp_template("::tpy::BigInt(static_cast<uint64_t>({0}))")
    def __init__(self, x: UInt16) -> None: ...
    @overload
    @cpp_template("::tpy::BigInt(static_cast<uint64_t>({0}))")
    def __init__(self, x: UInt32) -> None: ...
    @overload
    @cpp_template("::tpy::BigInt(static_cast<uint64_t>({0}))")
    def __init__(self, x: UInt64) -> None: ...
    @overload
    @cpp_template("::tpy::BigInt::from_float({0})")
    def __init__(self, x: float) -> None: ...
    @overload
    @cpp_template("::tpy::BigInt::from_str({0})")
    def __init__(self, x: str) -> None: ...
    @overload
    @cpp_template("::tpy::BigInt(static_cast<int32_t>({0}))")
    def __init__(self, x: bool) -> None: ...
    @overload
    @cpp_template("::tpy::BigInt(static_cast<int32_t>({0}))")
    def __init__(self, x: Char) -> None: ...
    @cpp_template("({self}) + ({0})")
    def __add__(self, other: int) -> int: ...
    @cpp_template("({self}) - ({0})")
    def __sub__(self, other: int) -> int: ...
    @cpp_template("({self}) * ({0})")
    def __mul__(self, other: int) -> int: ...
    @cpp_template("::tpy::truediv(static_cast<double>({self}), static_cast<double>({0}))")
    def __truediv__(self, other: int) -> float: ...
    @cpp_template("({self}) / ({0})")
    def __floordiv__(self, other: int) -> int: ...
    @cpp_template("({self}) % ({0})")
    def __mod__(self, other: int) -> int: ...
    @cpp_template("({self}).pow({0})")
    def __pow__(self, other: int) -> int: ...
    @cpp_template("({self}) << ({0})")
    def __lshift__(self, other: int) -> int: ...
    @cpp_template("({self}) >> ({0})")
    def __rshift__(self, other: int) -> int: ...
    @cpp_template("({self}) & ({0})")
    def __and__(self, other: int) -> int: ...
    @cpp_template("({self}) | ({0})")
    def __or__(self, other: int) -> int: ...
    @cpp_template("({self}) ^ ({0})")
    def __xor__(self, other: int) -> int: ...
    @cpp_template("({0}) + ({self})")
    def __radd__(self, other: int) -> int: ...
    @cpp_template("({0}) - ({self})")
    def __rsub__(self, other: int) -> int: ...
    @cpp_template("({0}) * ({self})")
    def __rmul__(self, other: int) -> int: ...
    @cpp_template("::tpy::truediv(static_cast<double>({0}), static_cast<double>({self}))")
    def __rtruediv__(self, other: int) -> float: ...
    @cpp_template("({0}) / ({self})")
    def __rfloordiv__(self, other: int) -> int: ...
    @cpp_template("({0}) % ({self})")
    def __rmod__(self, other: int) -> int: ...
    @cpp_template("({0}).pow({self})")
    def __rpow__(self, other: int) -> int: ...
    @cpp_template("({0}) << ({self})")
    def __rlshift__(self, other: int) -> int: ...
    @cpp_template("({0}) >> ({self})")
    def __rrshift__(self, other: int) -> int: ...
    @cpp_template("({0}) & ({self})")
    def __rand__(self, other: int) -> int: ...
    @cpp_template("({0}) | ({self})")
    def __ror__(self, other: int) -> int: ...
    @cpp_template("({0}) ^ ({self})")
    def __rxor__(self, other: int) -> int: ...
    @cpp_template("+({self})")
    def __pos__(self) -> int: ...
    @cpp_template("-({self})")
    def __neg__(self) -> int: ...
    @cpp_template("~({self})")
    def __invert__(self) -> int: ...
    @cpp_template("::tpy::__hash__({self})")
    @readonly
    @pure
    def __hash__(self) -> UInt64: ...
    @cpp_template("{self} == {0}")
    @readonly
    @pure
    def __eq__(self, other: int) -> bool: ...
    @cpp_template("{self} < {0}")
    @readonly
    @pure
    def __lt__(self, other: int) -> bool: ...


@builtin_type("builtins.float")
@native("double")
class float(Comparable, Equatable):
    @overload
    @cpp_template("0.0")
    def __init__(self) -> None: ...
    @overload
    @cpp_template("{0}")
    def __init__(self, x: float) -> None: ...
    @overload
    @cpp_template("static_cast<double>({0})")
    def __init__(self, x: Int32) -> None: ...
    @overload
    @cpp_template("static_cast<double>({0})")
    def __init__(self, x: int) -> None: ...
    @overload
    @cpp_template("static_cast<double>({0})")
    def __init__(self, x: bool) -> None: ...
    @overload
    @cpp_template("static_cast<double>({0})")
    def __init__(self, x: Float32) -> None: ...
    @overload
    @cpp_template("::tpy::float_from_str({0})")
    def __init__(self, x: str) -> None: ...
    @overload
    @cpp_template("({self}) + ({0})")
    def __add__(self, other: float) -> float: ...
    @overload
    @cpp_template("({self}) + static_cast<double>({0})")
    def __add__(self, other: int) -> float: ...
    @overload
    @cpp_template("({self}) + static_cast<double>({0})")
    def __add__(self, other: Int32) -> float: ...
    @overload
    @cpp_template("({self}) - ({0})")
    def __sub__(self, other: float) -> float: ...
    @overload
    @cpp_template("({self}) - static_cast<double>({0})")
    def __sub__(self, other: int) -> float: ...
    @overload
    @cpp_template("({self}) - static_cast<double>({0})")
    def __sub__(self, other: Int32) -> float: ...
    @overload
    @cpp_template("({self}) * ({0})")
    def __mul__(self, other: float) -> float: ...
    @overload
    @cpp_template("({self}) * static_cast<double>({0})")
    def __mul__(self, other: int) -> float: ...
    @overload
    @cpp_template("({self}) * static_cast<double>({0})")
    def __mul__(self, other: Int32) -> float: ...
    @overload
    @cpp_template("::tpy::truediv({self}, {0})")
    def __truediv__(self, other: float) -> float: ...
    @overload
    @cpp_template("::tpy::truediv({self}, static_cast<double>({0}))")
    def __truediv__(self, other: int) -> float: ...
    @overload
    @cpp_template("::tpy::truediv({self}, static_cast<double>({0}))")
    def __truediv__(self, other: Int32) -> float: ...
    @overload
    @cpp_template("::tpy::floordiv({self}, {0})")
    def __floordiv__(self, other: float) -> float: ...
    @overload
    @cpp_template("::tpy::floordiv({self}, static_cast<double>({0}))")
    def __floordiv__(self, other: int) -> float: ...
    @overload
    @cpp_template("::tpy::floordiv({self}, static_cast<double>({0}))")
    def __floordiv__(self, other: Int32) -> float: ...
    @overload
    @cpp_template("::tpy::fmod({self}, {0})")
    def __mod__(self, other: float) -> float: ...
    @overload
    @cpp_template("::tpy::fmod({self}, static_cast<double>({0}))")
    def __mod__(self, other: int) -> float: ...
    @overload
    @cpp_template("::tpy::fmod({self}, static_cast<double>({0}))")
    def __mod__(self, other: Int32) -> float: ...
    @overload
    @cpp_template("std::pow({self}, {0})")
    def __pow__(self, other: float) -> float: ...
    @overload
    @cpp_template("std::pow({self}, static_cast<double>({0}))")
    def __pow__(self, other: int) -> float: ...
    @overload
    @cpp_template("std::pow({self}, static_cast<double>({0}))")
    def __pow__(self, other: Int32) -> float: ...
    @cpp_template("+({self})")
    def __pos__(self) -> float: ...
    @cpp_template("-({self})")
    def __neg__(self) -> float: ...
    @overload
    @cpp_template("({0}) + ({self})")
    def __radd__(self, other: float) -> float: ...
    @overload
    @cpp_template("static_cast<double>({0}) + ({self})")
    def __radd__(self, other: int) -> float: ...
    @overload
    @cpp_template("static_cast<double>({0}) + ({self})")
    def __radd__(self, other: Int32) -> float: ...
    @overload
    @cpp_template("({0}) - ({self})")
    def __rsub__(self, other: float) -> float: ...
    @overload
    @cpp_template("static_cast<double>({0}) - ({self})")
    def __rsub__(self, other: int) -> float: ...
    @overload
    @cpp_template("static_cast<double>({0}) - ({self})")
    def __rsub__(self, other: Int32) -> float: ...
    @overload
    @cpp_template("({0}) * ({self})")
    def __rmul__(self, other: float) -> float: ...
    @overload
    @cpp_template("static_cast<double>({0}) * ({self})")
    def __rmul__(self, other: int) -> float: ...
    @overload
    @cpp_template("static_cast<double>({0}) * ({self})")
    def __rmul__(self, other: Int32) -> float: ...
    @overload
    @cpp_template("::tpy::truediv({0}, {self})")
    def __rtruediv__(self, other: float) -> float: ...
    @overload
    @cpp_template("::tpy::truediv(static_cast<double>({0}), {self})")
    def __rtruediv__(self, other: int) -> float: ...
    @overload
    @cpp_template("::tpy::truediv(static_cast<double>({0}), {self})")
    def __rtruediv__(self, other: Int32) -> float: ...
    @overload
    @cpp_template("::tpy::floordiv({0}, {self})")
    def __rfloordiv__(self, other: float) -> float: ...
    @overload
    @cpp_template("::tpy::floordiv(static_cast<double>({0}), {self})")
    def __rfloordiv__(self, other: int) -> float: ...
    @overload
    @cpp_template("::tpy::floordiv(static_cast<double>({0}), {self})")
    def __rfloordiv__(self, other: Int32) -> float: ...
    @overload
    @cpp_template("::tpy::fmod({0}, {self})")
    def __rmod__(self, other: float) -> float: ...
    @overload
    @cpp_template("::tpy::fmod(static_cast<double>({0}), {self})")
    def __rmod__(self, other: int) -> float: ...
    @overload
    @cpp_template("::tpy::fmod(static_cast<double>({0}), {self})")
    def __rmod__(self, other: Int32) -> float: ...
    @overload
    @cpp_template("std::pow({0}, {self})")
    def __rpow__(self, other: float) -> float: ...
    @overload
    @cpp_template("std::pow(static_cast<double>({0}), {self})")
    def __rpow__(self, other: int) -> float: ...
    @overload
    @cpp_template("std::pow(static_cast<double>({0}), {self})")
    def __rpow__(self, other: Int32) -> float: ...
    @cpp_template("::tpy::__hash__({self})")
    @readonly
    @pure
    def __hash__(self) -> UInt64: ...
    @cpp_template("{self} == {0}")
    @readonly
    @pure
    def __eq__(self, other: float) -> bool: ...
    @cpp_template("{self} < {0}")
    @readonly
    @pure
    def __lt__(self, other: float) -> bool: ...

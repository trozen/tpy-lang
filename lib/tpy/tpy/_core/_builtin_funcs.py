# tpy: cpp_namespace("tpystd::builtins")
from typing import overload
from ._typing import Sized, Iterator, Iterable
from tpy import (
    Int8, Int16, Int32, Int64, UInt8, UInt16, UInt32, UInt64,
    Char, String, StrView, Float32, Own, readonly, pure,
)
from ._types import (
    AnyFixedInt,
    Hashable, Representable, Stringable, NativeIterable, Truthy, Comparable, Equatable,
)
from tpy.extern import native, cpp_template, builtin_type
from tpy import error_return


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

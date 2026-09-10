# tpy: native_module
# tpy: cpp_namespace("tpystd::builtins")
from .._typing import Sized, Sequence, Iterator, Iterable
from .._bootstrap._decorators import readonly, pure, error_return, Own, Fn, dispatch
from .._core._types import (
    Int8, Int16, Int32, Int64, UInt8, UInt16, UInt32, UInt64,
    Char, String, StrView, Float32, AnyFixedInt,
    Hashable, Representable, Stringable, NativeIterable, Truthy, Comparable, Equatable,
)
from .._bootstrap._extern import native, cpp_template, builtin_type, builtin_function, type_param_default, DefaultInt


@pure
@readonly
@native("tpy::__len__")
def len(x: Sized) -> Int32: ...


@pure
@readonly
@cpp_template("::tpy::repr_of({0})")
def repr(x: Representable) -> str: ...


@pure
@readonly
@native("tpy::__hash__")
def hash(x: Hashable) -> UInt64: ...


@dispatch
@pure
@readonly
@cpp_template("static_cast<char>({0})")
def chr(i: Int32) -> Char: ...

@dispatch
@pure
@readonly
@cpp_template("static_cast<char>(({0}).to_fixed_check<int32_t>())")
def chr(i: int) -> Char: ...


@dispatch
@pure
@readonly
@cpp_template("static_cast<int32_t>(static_cast<unsigned char>({0}))")
def ord(c: Char) -> Int32: ...

@dispatch
@readonly
@native("tpy::ord_str")
def ord(s: str) -> Int32: ...

@dispatch
@readonly
@native("tpy::ord_str")
def ord(s: String) -> Int32: ...

@dispatch
@readonly
@native("tpy::ord_str")
def ord(s: StrView) -> Int32: ...


@dispatch
@pure
@readonly
@native("std::abs")
def abs(x: Int32) -> Int32: ...

@dispatch
@pure
@readonly
@native("tpy::BigInt::abs")
def abs(x: int) -> int: ...

@dispatch
@pure
@readonly
@native("std::fabs")
def abs(x: float) -> float: ...


@dispatch
@pure
@readonly
@native("std::min")
def min(a: Int32, b: Int32) -> Int32: ...

@dispatch
@pure
@readonly
@native("tpy::min3")
def min(a: Int32, b: Int32, c: Int32) -> Int32: ...

@dispatch
@pure
@readonly
@native("std::min")
def min(a: int, b: int) -> int: ...

@dispatch
@pure
@readonly
@native("tpy::min3")
def min(a: int, b: int, c: int) -> int: ...

@dispatch
@pure
@readonly
@native("std::fmin")
def min(a: float, b: float) -> float: ...

@dispatch
@pure
@readonly
@cpp_template("std::fmin(std::fmin({0}, {1}), {2})")
def min(a: float, b: float, c: float) -> float: ...

@dispatch
@pure
@readonly
@cpp_template("::tpy::min_key({0}, {1}, {2})")
def min[T, K: Comparable](a: T, b: T, key: Fn[[T], K]) -> T: ...

@dispatch
@pure
@readonly
@cpp_template("::tpy::min3_key({0}, {1}, {2}, {3})")
def min[T, K: Comparable](a: T, b: T, c: T, key: Fn[[T], K]) -> T: ...


@dispatch
@pure
@readonly
@native("std::max")
def max(a: Int32, b: Int32) -> Int32: ...

@dispatch
@pure
@readonly
@native("tpy::max3")
def max(a: Int32, b: Int32, c: Int32) -> Int32: ...

@dispatch
@pure
@readonly
@native("std::max")
def max(a: int, b: int) -> int: ...

@dispatch
@pure
@readonly
@native("tpy::max3")
def max(a: int, b: int, c: int) -> int: ...

@dispatch
@pure
@readonly
@native("std::fmax")
def max(a: float, b: float) -> float: ...

@dispatch
@pure
@readonly
@cpp_template("std::fmax(std::fmax({0}, {1}), {2})")
def max(a: float, b: float, c: float) -> float: ...

@dispatch
@pure
@readonly
@cpp_template("::tpy::max_key({0}, {1}, {2})")
def max[T, K: Comparable](a: T, b: T, key: Fn[[T], K]) -> T: ...

@dispatch
@pure
@readonly
@cpp_template("::tpy::max3_key({0}, {1}, {2}, {3})")
def max[T, K: Comparable](a: T, b: T, c: T, key: Fn[[T], K]) -> T: ...


# pow(x, y) -- checked exponentiation
@dispatch
@pure
@readonly
@cpp_template("({0}).pow({1})")
def pow(x: int, y: int) -> int: ...

@dispatch
@pure
@readonly
@native("std::pow")
def pow(x: float, y: float) -> float: ...

@dispatch
@pure
@readonly
@native("tpy::pow_check<int8_t>")
def pow(x: Int8, y: Int8) -> Int8: ...

@dispatch
@pure
@readonly
@native("tpy::pow_check<int16_t>")
def pow(x: Int16, y: Int16) -> Int16: ...

@dispatch
@pure
@readonly
@native("tpy::pow_check<int32_t>")
def pow(x: Int32, y: Int32) -> Int32: ...

@dispatch
@pure
@readonly
@native("tpy::pow_check<int64_t>")
def pow(x: Int64, y: Int64) -> Int64: ...

@dispatch
@pure
@readonly
@native("tpy::pow_check<uint8_t>")
def pow(x: UInt8, y: UInt8) -> UInt8: ...

@dispatch
@pure
@readonly
@native("tpy::pow_check<uint16_t>")
def pow(x: UInt16, y: UInt16) -> UInt16: ...

@dispatch
@pure
@readonly
@native("tpy::pow_check<uint32_t>")
def pow(x: UInt32, y: UInt32) -> UInt32: ...

@dispatch
@pure
@readonly
@native("tpy::pow_check<uint64_t>")
def pow(x: UInt64, y: UInt64) -> UInt64: ...


# divmod(a, b) -> tuple[T, T]
@dispatch
@pure
@readonly
@native("tpy::divmod_bigint")
def divmod(a: int, b: int) -> tuple[int, int]: ...

@dispatch
@pure
@readonly
@native("tpy::divmod_float")
def divmod(a: float, b: float) -> tuple[float, float]: ...

@dispatch
@pure
@readonly
@native("tpy::divmod_fixed<int8_t>")
def divmod(a: Int8, b: Int8) -> tuple[Int8, Int8]: ...

@dispatch
@pure
@readonly
@native("tpy::divmod_fixed<int16_t>")
def divmod(a: Int16, b: Int16) -> tuple[Int16, Int16]: ...

@dispatch
@pure
@readonly
@native("tpy::divmod_fixed<int32_t>")
def divmod(a: Int32, b: Int32) -> tuple[Int32, Int32]: ...

@dispatch
@pure
@readonly
@native("tpy::divmod_fixed<int64_t>")
def divmod(a: Int64, b: Int64) -> tuple[Int64, Int64]: ...

@dispatch
@pure
@readonly
@native("tpy::divmod_fixed<uint8_t>")
def divmod(a: UInt8, b: UInt8) -> tuple[UInt8, UInt8]: ...

@dispatch
@pure
@readonly
@native("tpy::divmod_fixed<uint16_t>")
def divmod(a: UInt16, b: UInt16) -> tuple[UInt16, UInt16]: ...

@dispatch
@pure
@readonly
@native("tpy::divmod_fixed<uint32_t>")
def divmod(a: UInt32, b: UInt32) -> tuple[UInt32, UInt32]: ...

@dispatch
@pure
@readonly
@native("tpy::divmod_fixed<uint64_t>")
def divmod(a: UInt64, b: UInt64) -> tuple[UInt64, UInt64]: ...


@error_return(StopIteration)
@native("tpy::next")
def next[T](it: Iterator[T]) -> T: ...


# TODO: make non-native once regular functions can return protocol types
@readonly
@type_param_default(T=DefaultInt)
@native("tpy::__iter__")
def iter[T](x: Iterable[T]) -> Iterator[T]: ...


# round(): generic float->T with default T=DefaultInt (resolved via --default-int),
# identity for integer types, and ndigits variants.
@dispatch
@type_param_default(T=DefaultInt)
@pure
@readonly
@cpp_template("::tpy::round_to<{T}>({0})")
def round[T](x: float) -> T: ...

@dispatch
@pure
@readonly
@native("tpy::round_float")
def round(x: float, ndigits: Int32) -> float: ...

@dispatch
@pure
@readonly
@cpp_template("({0})")
def round(x: Int8) -> Int8: ...
@dispatch
@pure
@readonly
@native("tpy::round_fixed<int8_t>")
def round(x: Int8, ndigits: Int32) -> Int8: ...

@dispatch
@pure
@readonly
@cpp_template("({0})")
def round(x: Int16) -> Int16: ...
@dispatch
@pure
@readonly
@native("tpy::round_fixed<int16_t>")
def round(x: Int16, ndigits: Int32) -> Int16: ...

@dispatch
@pure
@readonly
@cpp_template("({0})")
def round(x: Int32) -> Int32: ...
@dispatch
@pure
@readonly
@native("tpy::round_fixed<int32_t>")
def round(x: Int32, ndigits: Int32) -> Int32: ...

@dispatch
@pure
@readonly
@cpp_template("({0})")
def round(x: Int64) -> Int64: ...
@dispatch
@pure
@readonly
@native("tpy::round_fixed<int64_t>")
def round(x: Int64, ndigits: Int32) -> Int64: ...

@dispatch
@pure
@readonly
@cpp_template("({0})")
def round(x: UInt8) -> UInt8: ...
@dispatch
@pure
@readonly
@native("tpy::round_fixed<uint8_t>")
def round(x: UInt8, ndigits: Int32) -> UInt8: ...

@dispatch
@pure
@readonly
@cpp_template("({0})")
def round(x: UInt16) -> UInt16: ...
@dispatch
@pure
@readonly
@native("tpy::round_fixed<uint16_t>")
def round(x: UInt16, ndigits: Int32) -> UInt16: ...

@dispatch
@pure
@readonly
@cpp_template("({0})")
def round(x: UInt32) -> UInt32: ...
@dispatch
@pure
@readonly
@native("tpy::round_fixed<uint32_t>")
def round(x: UInt32, ndigits: Int32) -> UInt32: ...

@dispatch
@pure
@readonly
@cpp_template("({0})")
def round(x: UInt64) -> UInt64: ...
@dispatch
@pure
@readonly
@native("tpy::round_fixed<uint64_t>")
def round(x: UInt64, ndigits: Int32) -> UInt64: ...

@dispatch
@pure
@readonly
@cpp_template("({0})")
def round(x: int) -> int: ...
@dispatch
@pure
@readonly
@native("tpy::round_bigint")
def round(x: int, ndigits: Int32) -> int: ...


# -- all / any --

@pure
@readonly
@type_param_default(T=DefaultInt)
@native("tpy::builtin_all")
def all[T: Truthy](iterable: Iterable[T]) -> bool: ...

@pure
@readonly
@type_param_default(T=DefaultInt)
@native("tpy::builtin_any")
def any[T: Truthy](iterable: Iterable[T]) -> bool: ...


# -- sum --

@dispatch
@pure
@readonly
@native("tpy::builtin_sum<int32_t>")
def sum(iterable: Iterable[Int32]) -> Int32: ...

@dispatch
@pure
@readonly
@native("tpy::builtin_sum_start<int32_t>")
def sum(iterable: Iterable[Int32], start: Int32) -> Int32: ...

@dispatch
@pure
@readonly
@native("tpy::builtin_sum<int64_t>")
def sum(iterable: Iterable[Int64]) -> Int64: ...

@dispatch
@pure
@readonly
@native("tpy::builtin_sum_start<int64_t>")
def sum(iterable: Iterable[Int64], start: Int64) -> Int64: ...

@dispatch
@pure
@readonly
@cpp_template("::tpy::builtin_sum<{T}>({0})")
def sum[T: AnyFixedInt](iterable: Iterable[T]) -> T: ...

@dispatch
@pure
@readonly
@cpp_template("::tpy::builtin_sum_start<{T}>({0}, {1})")
def sum[T: AnyFixedInt](iterable: Iterable[T], start: T) -> T: ...

@dispatch
@pure
@readonly
@native("tpy::builtin_sum_bigint")
def sum(iterable: Iterable[int]) -> int: ...

@dispatch
@pure
@readonly
@native("tpy::builtin_sum_start_bigint")
def sum(iterable: Iterable[int], start: int) -> int: ...

@dispatch
@pure
@readonly
@native("tpy::builtin_sum_float")
def sum(iterable: Iterable[float]) -> float: ...

@dispatch
@pure
@readonly
@native("tpy::builtin_sum_start_float")
def sum(iterable: Iterable[float], start: float) -> float: ...


# -- sorted --

@dispatch
@pure
@readonly
@type_param_default(T=DefaultInt)
@cpp_template("::tpy::builtin_sorted<{T}>({0})")
def sorted[T: Comparable](iterable: Iterable[T]) -> Own[list[T]]: ...

@dispatch
@pure
@readonly
@cpp_template("::tpy::builtin_sorted_key<{T}>({0}, {1})")
def sorted[T, K: Comparable](iterable: Iterable[T], key: Fn[[T], K]) -> Own[list[T]]: ...


# -- bin / hex / oct --

@dispatch
@pure
@readonly
@native("tpy::builtin_bin")
def bin[T: AnyFixedInt](x: T) -> str: ...

@dispatch
@pure
@readonly
@native("tpy::builtin_bin_bigint")
def bin(x: int) -> str: ...


@dispatch
@pure
@readonly
@native("tpy::builtin_hex")
def hex[T: AnyFixedInt](x: T) -> str: ...

@dispatch
@pure
@readonly
@native("tpy::builtin_hex_bigint")
def hex(x: int) -> str: ...


@dispatch
@pure
@readonly
@native("tpy::builtin_oct")
def oct[T: AnyFixedInt](x: T) -> str: ...

@dispatch
@pure
@readonly
@native("tpy::builtin_oct_bigint")
def oct(x: int) -> str: ...


# -- enumerate --

@dispatch
@readonly
@type_param_default(T=DefaultInt)
@cpp_template("::tpy::builtin_enumerate<{T}>({0})")
def enumerate[T](iterable: Iterable[T]) -> Iterator[tuple[Int32, T]]: ...

@dispatch
@readonly
@type_param_default(T=DefaultInt)
@cpp_template("::tpy::builtin_enumerate_start<{T}>({0}, {1})")
def enumerate[T](iterable: Iterable[T], start: Int32) -> Iterator[tuple[Int32, T]]: ...


# -- reversed --

@readonly
@type_param_default(T=DefaultInt)
@cpp_template("::tpy::builtin_reversed<{T}>({0})")
def reversed[T](seq: Sequence[T]) -> Iterator[T]: ...


# -- zip --

@dispatch
@readonly
@cpp_template("::tpy::builtin_zip<{T1}, {T2}>({0}, {1})")
def zip[T1, T2](iter1: Iterable[T1], iter2: Iterable[T2]) -> Iterator[tuple[T1, T2]]: ...

@dispatch
@readonly
@cpp_template("::tpy::builtin_zip<{T1}, {T2}, {T3}>({0}, {1}, {2})")
def zip[T1, T2, T3](iter1: Iterable[T1], iter2: Iterable[T2], iter3: Iterable[T3]) -> Iterator[tuple[T1, T2, T3]]: ...

@dispatch
@readonly
@cpp_template("::tpy::builtin_zip<{T1}, {T2}, {T3}, {T4}>({0}, {1}, {2}, {3})")
def zip[T1, T2, T3, T4](iter1: Iterable[T1], iter2: Iterable[T2], iter3: Iterable[T3], iter4: Iterable[T4]) -> Iterator[tuple[T1, T2, T3, T4]]: ...

@dispatch
@readonly
@cpp_template("::tpy::builtin_zip<{T1}, {T2}, {T3}, {T4}, {T5}>({0}, {1}, {2}, {3}, {4})")
def zip[T1, T2, T3, T4, T5](iter1: Iterable[T1], iter2: Iterable[T2], iter3: Iterable[T3], iter4: Iterable[T4], iter5: Iterable[T5]) -> Iterator[tuple[T1, T2, T3, T4, T5]]: ...


# -- map --

@dispatch
@readonly
@cpp_template("::tpy::builtin_map<{T}, {U}>({0}, {1})")
def map[T, U](fn: Fn[[T], U], iterable: Iterable[T]) -> Iterator[U]: ...

@dispatch
@readonly
@cpp_template("::tpy::builtin_map_n<{U}>({0}, {1}, {2})")
def map[T1, T2, U](fn: Fn[[T1, T2], U], iter1: Iterable[T1], iter2: Iterable[T2]) -> Iterator[U]: ...

@dispatch
@readonly
@cpp_template("::tpy::builtin_map_n<{U}>({0}, {1}, {2}, {3})")
def map[T1, T2, T3, U](fn: Fn[[T1, T2, T3], U], iter1: Iterable[T1], iter2: Iterable[T2], iter3: Iterable[T3]) -> Iterator[U]: ...

@dispatch
@readonly
@cpp_template("::tpy::builtin_map_n<{U}>({0}, {1}, {2}, {3}, {4})")
def map[T1, T2, T3, T4, U](fn: Fn[[T1, T2, T3, T4], U], iter1: Iterable[T1], iter2: Iterable[T2], iter3: Iterable[T3], iter4: Iterable[T4]) -> Iterator[U]: ...

@dispatch
@readonly
@cpp_template("::tpy::builtin_map_n<{U}>({0}, {1}, {2}, {3}, {4}, {5})")
def map[T1, T2, T3, T4, T5, U](fn: Fn[[T1, T2, T3, T4, T5], U], iter1: Iterable[T1], iter2: Iterable[T2], iter3: Iterable[T3], iter4: Iterable[T4], iter5: Iterable[T5]) -> Iterator[U]: ...


# -- filter --

@dispatch
@readonly
@cpp_template("::tpy::builtin_filter<{T}>({0}, {1})")
def filter[T](fn: Fn[[T], bool], iterable: Iterable[T]) -> Iterator[T]: ...

@dispatch
@readonly
@cpp_template("::tpy::builtin_filter_truthy<{T}>({1})")
def filter[T](fn: None, iterable: Iterable[T]) -> Iterator[T]: ...


# Read one line from stdin (the trailing newline is stripped). Returns
# the line as a heap-allocated `String`. Mirrors CPython's `input()`
# minus the optional prompt arg, which is future work.
@native("tpy::input_line")
def input() -> String: ...


# -- Special-handling builtins (custom sema/codegen, signatures are illustrative) --

@builtin_function("builtins.print")
def print(*args, sep: str = " ", end: str = "\n"): ...

@builtin_function("builtins.isinstance")
def isinstance(x, type_or_tuple) -> bool: ...

@builtin_function("builtins.getattr")
def getattr(obj, name, default=None): ...

@builtin_function("builtins.setattr")
def setattr(obj, name, value): ...

@builtin_function("builtins.delattr")
def delattr(obj, name): ...

@builtin_function("builtins.hasattr")
def hasattr(obj, name) -> bool: ...

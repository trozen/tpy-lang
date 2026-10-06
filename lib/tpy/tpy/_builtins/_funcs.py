# tpy: native_module
# tpy: cpp_namespace("tpystd::builtins")
from .._typing import Sized, Sequence, Iterator, Iterable
from .._bootstrap._decorators import readonly, pure, error_return, Own, Fn, dispatch
from .._core._types import (
    int8, int16, int32, int64, uint8, uint16, uint32, uint64,
    char, String, StrView, float32, AnyFixedInt,
    Hashable, Representable, Stringable, NativeIterable, Truthy, Comparable, Equatable,
    ComparableValue, ValueType, ComparableRef, ReferenceType,
)
from .._bootstrap._extern import native, cpp_template, builtin_type, builtin_function, type_param_default, DefaultInt


@pure
@readonly
@native("tpy::__len__")
def len(x: Sized) -> int32: ...


@pure
@readonly
@cpp_template("::tpy::repr_of({0})")
def repr(x: Representable) -> str: ...


@pure
@readonly
@native("tpy::__hash__")
def hash(x: Hashable) -> uint64: ...


@dispatch
@pure
@readonly
@cpp_template("static_cast<char>({0})")
def chr(i: int32) -> char: ...

@dispatch
@pure
@readonly
@cpp_template("static_cast<char>(({0}).to_fixed_check<int32_t>())")
def chr(i: int) -> char: ...


@dispatch
@pure
@readonly
@cpp_template("static_cast<int32_t>(static_cast<unsigned char>({0}))")
def ord(c: char) -> int32: ...

@dispatch
@readonly
@native("tpy::ord_str")
def ord(s: str) -> int32: ...

@dispatch
@readonly
@native("tpy::ord_str")
def ord(s: String) -> int32: ...

@dispatch
@readonly
@native("tpy::ord_str")
def ord(s: StrView) -> int32: ...


@dispatch
@pure
@readonly
@native("std::abs")
def abs(x: int32) -> int32: ...

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
@cpp_template("::std::min<{T}>({0}, {1})")
def min[T: AnyFixedInt](a: T, b: T) -> T: ...

@dispatch
@pure
@readonly
@cpp_template("::tpy::min3<{T}>({0}, {1}, {2})")
def min[T: AnyFixedInt](a: T, b: T, c: T) -> T: ...

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
@cpp_template("::tpy::min_key({0}, {1}, {2})", borrows=("a", "b"))
def min[T, K: Comparable](a: T, b: T, key: Fn[[T], K]) -> T: ...

@dispatch
@pure
@readonly
@cpp_template("::tpy::min3_key({0}, {1}, {2}, {3})", borrows=("a", "b", "c"))
def min[T, K: Comparable](a: T, b: T, c: T, key: Fn[[T], K]) -> T: ...

@dispatch
@pure
@readonly
@type_param_default(T=DefaultInt)
@cpp_template("::tpy::builtin_min<{T}>({0})")
def min[T: ComparableValue](iterable: Iterable[T]) -> T: ...

@dispatch
@pure
@readonly
@type_param_default(T=DefaultInt)
@cpp_template("::tpy::builtin_min_key<{T}>({0}, {1})")
def min[T: ValueType, K: Comparable](iterable: Iterable[T], key: Fn[[T], K]) -> T: ...

# A class instance (or list, dict, ...) comes back as the element itself,
# borrowed from a container source, as Python returns the object; over any
# other source it is a copy.
@dispatch
@pure
@readonly
@cpp_template("::tpy::builtin_min_elem({0})", element_of=("iterable",))
def min[T: ComparableRef](iterable: readonly[Iterable[T]]) -> T: ...

@dispatch
@pure
@readonly
@cpp_template("::tpy::builtin_min_elem_key({0}, {1})", element_of=("iterable",))
def min[T: ReferenceType, K: Comparable](iterable: readonly[Iterable[T]], key: Fn[[T], K]) -> T: ...


@dispatch
@pure
@readonly
@cpp_template("::std::max<{T}>({0}, {1})")
def max[T: AnyFixedInt](a: T, b: T) -> T: ...

@dispatch
@pure
@readonly
@cpp_template("::tpy::max3<{T}>({0}, {1}, {2})")
def max[T: AnyFixedInt](a: T, b: T, c: T) -> T: ...

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
@cpp_template("::tpy::max_key({0}, {1}, {2})", borrows=("a", "b"))
def max[T, K: Comparable](a: T, b: T, key: Fn[[T], K]) -> T: ...

@dispatch
@pure
@readonly
@cpp_template("::tpy::max3_key({0}, {1}, {2}, {3})", borrows=("a", "b", "c"))
def max[T, K: Comparable](a: T, b: T, c: T, key: Fn[[T], K]) -> T: ...

@dispatch
@pure
@readonly
@type_param_default(T=DefaultInt)
@cpp_template("::tpy::builtin_max<{T}>({0})")
def max[T: ComparableValue](iterable: Iterable[T]) -> T: ...

@dispatch
@pure
@readonly
@type_param_default(T=DefaultInt)
@cpp_template("::tpy::builtin_max_key<{T}>({0}, {1})")
def max[T: ValueType, K: Comparable](iterable: Iterable[T], key: Fn[[T], K]) -> T: ...

# A class instance (or list, dict, ...) comes back as the element itself,
# borrowed from a container source, as Python returns the object; over any
# other source it is a copy.
@dispatch
@pure
@readonly
@cpp_template("::tpy::builtin_max_elem({0})", element_of=("iterable",))
def max[T: ComparableRef](iterable: readonly[Iterable[T]]) -> T: ...

@dispatch
@pure
@readonly
@cpp_template("::tpy::builtin_max_elem_key({0}, {1})", element_of=("iterable",))
def max[T: ReferenceType, K: Comparable](iterable: readonly[Iterable[T]], key: Fn[[T], K]) -> T: ...


# pow(x, y) -- checked exponentiation
@dispatch
@pure
@readonly
@cpp_template("({0}).pow({1})")
def pow(base: int, exp: int) -> int: ...

@dispatch
@pure
@readonly
@native("std::pow")
def pow(base: float, exp: float) -> float: ...

@dispatch
@pure
@readonly
@native("tpy::pow_check<int8_t>")
def pow(base: int8, exp: int8) -> int8: ...

@dispatch
@pure
@readonly
@native("tpy::pow_check<int16_t>")
def pow(base: int16, exp: int16) -> int16: ...

@dispatch
@pure
@readonly
@native("tpy::pow_check<int32_t>")
def pow(base: int32, exp: int32) -> int32: ...

@dispatch
@pure
@readonly
@native("tpy::pow_check<int64_t>")
def pow(base: int64, exp: int64) -> int64: ...

@dispatch
@pure
@readonly
@native("tpy::pow_check<uint8_t>")
def pow(base: uint8, exp: uint8) -> uint8: ...

@dispatch
@pure
@readonly
@native("tpy::pow_check<uint16_t>")
def pow(base: uint16, exp: uint16) -> uint16: ...

@dispatch
@pure
@readonly
@native("tpy::pow_check<uint32_t>")
def pow(base: uint32, exp: uint32) -> uint32: ...

@dispatch
@pure
@readonly
@native("tpy::pow_check<uint64_t>")
def pow(base: uint64, exp: uint64) -> uint64: ...


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
def divmod(a: int8, b: int8) -> tuple[int8, int8]: ...

@dispatch
@pure
@readonly
@native("tpy::divmod_fixed<int16_t>")
def divmod(a: int16, b: int16) -> tuple[int16, int16]: ...

@dispatch
@pure
@readonly
@native("tpy::divmod_fixed<int32_t>")
def divmod(a: int32, b: int32) -> tuple[int32, int32]: ...

@dispatch
@pure
@readonly
@native("tpy::divmod_fixed<int64_t>")
def divmod(a: int64, b: int64) -> tuple[int64, int64]: ...

@dispatch
@pure
@readonly
@native("tpy::divmod_fixed<uint8_t>")
def divmod(a: uint8, b: uint8) -> tuple[uint8, uint8]: ...

@dispatch
@pure
@readonly
@native("tpy::divmod_fixed<uint16_t>")
def divmod(a: uint16, b: uint16) -> tuple[uint16, uint16]: ...

@dispatch
@pure
@readonly
@native("tpy::divmod_fixed<uint32_t>")
def divmod(a: uint32, b: uint32) -> tuple[uint32, uint32]: ...

@dispatch
@pure
@readonly
@native("tpy::divmod_fixed<uint64_t>")
def divmod(a: uint64, b: uint64) -> tuple[uint64, uint64]: ...


@dispatch
@error_return(StopIteration)
@native("tpy::next")
def next[T](it: Iterator[T]) -> T: ...

@dispatch
@native("tpy::next_or")
def next[T: ValueType](it: Iterator[T], default: T) -> T: ...

# A class instance comes back as the step's element or as the default
# itself, as Python returns the object. The iterator never lends: a step is
# valid only until the next one, so a result that may come from it is a value
# wherever it is held (`element_of`), and lives only through an in-place use.
@dispatch
@native("tpy::next_or", borrows=("default",), element_of=("it",))
def next[T: ReferenceType](it: Iterator[T], default: T) -> T: ...


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
def round[T](number: float) -> T: ...

@dispatch
@pure
@readonly
@native("tpy::round_float")
def round(number: float, ndigits: int32) -> float: ...

@dispatch
@pure
@readonly
@cpp_template("({0})")
def round(number: int8) -> int8: ...
@dispatch
@pure
@readonly
@native("tpy::round_fixed<int8_t>")
def round(number: int8, ndigits: int32) -> int8: ...

@dispatch
@pure
@readonly
@cpp_template("({0})")
def round(number: int16) -> int16: ...
@dispatch
@pure
@readonly
@native("tpy::round_fixed<int16_t>")
def round(number: int16, ndigits: int32) -> int16: ...

@dispatch
@pure
@readonly
@cpp_template("({0})")
def round(number: int32) -> int32: ...
@dispatch
@pure
@readonly
@native("tpy::round_fixed<int32_t>")
def round(number: int32, ndigits: int32) -> int32: ...

@dispatch
@pure
@readonly
@cpp_template("({0})")
def round(number: int64) -> int64: ...
@dispatch
@pure
@readonly
@native("tpy::round_fixed<int64_t>")
def round(number: int64, ndigits: int32) -> int64: ...

@dispatch
@pure
@readonly
@cpp_template("({0})")
def round(number: uint8) -> uint8: ...
@dispatch
@pure
@readonly
@native("tpy::round_fixed<uint8_t>")
def round(number: uint8, ndigits: int32) -> uint8: ...

@dispatch
@pure
@readonly
@cpp_template("({0})")
def round(number: uint16) -> uint16: ...
@dispatch
@pure
@readonly
@native("tpy::round_fixed<uint16_t>")
def round(number: uint16, ndigits: int32) -> uint16: ...

@dispatch
@pure
@readonly
@cpp_template("({0})")
def round(number: uint32) -> uint32: ...
@dispatch
@pure
@readonly
@native("tpy::round_fixed<uint32_t>")
def round(number: uint32, ndigits: int32) -> uint32: ...

@dispatch
@pure
@readonly
@cpp_template("({0})")
def round(number: uint64) -> uint64: ...
@dispatch
@pure
@readonly
@native("tpy::round_fixed<uint64_t>")
def round(number: uint64, ndigits: int32) -> uint64: ...

@dispatch
@pure
@readonly
@cpp_template("({0})")
def round(number: int) -> int: ...
@dispatch
@pure
@readonly
@native("tpy::round_bigint")
def round(number: int, ndigits: int32) -> int: ...


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
def sum(iterable: Iterable[int32]) -> int32: ...

@dispatch
@pure
@readonly
@native("tpy::builtin_sum_start<int32_t>")
def sum(iterable: Iterable[int32], start: int32) -> int32: ...

@dispatch
@pure
@readonly
@native("tpy::builtin_sum<int64_t>")
def sum(iterable: Iterable[int64]) -> int64: ...

@dispatch
@pure
@readonly
@native("tpy::builtin_sum_start<int64_t>")
def sum(iterable: Iterable[int64], start: int64) -> int64: ...

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

@dispatch
@pure
@readonly
@type_param_default(T=DefaultInt)
@cpp_template("::tpy::builtin_sorted<{T}>({0}, {1})")
def sorted[T: Comparable](iterable: Iterable[T], reverse: bool) -> Own[list[T]]: ...

@dispatch
@pure
@readonly
@cpp_template("::tpy::builtin_sorted_key<{T}>({0}, {1}, {2})")
def sorted[T, K: Comparable](iterable: Iterable[T], key: Fn[[T], K], reverse: bool) -> Own[list[T]]: ...


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
@cpp_template("::tpy::builtin_enumerate({0})")
def enumerate[T](iterable: Iterable[T]) -> Iterator[tuple[int32, T]]: ...

@dispatch
@readonly
@type_param_default(T=DefaultInt)
@cpp_template("::tpy::builtin_enumerate({0}, {1})")
def enumerate[T](iterable: Iterable[T], start: int32) -> Iterator[tuple[int32, T]]: ...


# -- reversed --

@readonly
@type_param_default(T=DefaultInt)
@cpp_template("::tpy::builtin_reversed({0})")
def reversed[T](seq: Sequence[T]) -> Iterator[T]: ...


# -- zip --

@dispatch
@readonly
@cpp_template("::tpy::builtin_zip({0}, {1})")
def zip[T1, T2](iter1: Iterable[T1], iter2: Iterable[T2]) -> Iterator[tuple[T1, T2]]: ...

@dispatch
@readonly
@cpp_template("::tpy::builtin_zip({0}, {1}, {2})")
def zip[T1, T2, T3](iter1: Iterable[T1], iter2: Iterable[T2], iter3: Iterable[T3]) -> Iterator[tuple[T1, T2, T3]]: ...

@dispatch
@readonly
@cpp_template("::tpy::builtin_zip({0}, {1}, {2}, {3})")
def zip[T1, T2, T3, T4](iter1: Iterable[T1], iter2: Iterable[T2], iter3: Iterable[T3], iter4: Iterable[T4]) -> Iterator[tuple[T1, T2, T3, T4]]: ...

@dispatch
@readonly
@cpp_template("::tpy::builtin_zip({0}, {1}, {2}, {3}, {4})")
def zip[T1, T2, T3, T4, T5](iter1: Iterable[T1], iter2: Iterable[T2], iter3: Iterable[T3], iter4: Iterable[T4], iter5: Iterable[T5]) -> Iterator[tuple[T1, T2, T3, T4, T5]]: ...


# -- map --

@dispatch
@readonly
@cpp_template("::tpy::builtin_map<{U}>({0}, {1})")
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
@cpp_template("::tpy::builtin_filter({0}, {1})")
def filter[T](fn: Fn[[T], bool], iterable: Iterable[T]) -> Iterator[T]: ...

@dispatch
@readonly
@cpp_template("::tpy::builtin_filter_truthy({1})")
def filter[T](fn: None, iterable: Iterable[T]) -> Iterator[T]: ...


# Read one line from stdin (the trailing newline is stripped). Returns
# the line as a heap-allocated `String`. Mirrors CPython's `input()`:
# the prompt form writes the prompt to stdout without a newline and
# flushes before reading, and EOF raises EOFError.
@dispatch
@native("tpy::input_line", checks_signals=True)
def input() -> String: ...

@dispatch
@native("tpy::input_line", checks_signals=True)
def input(prompt: str) -> String: ...


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

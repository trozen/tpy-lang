# tpy: native_module
# tpy: cpp_namespace("tpystd::builtins")
from typing import overload, Sized, Iterator, Iterable
from tpy import (
    Int8, Int16, Int32, Int64, UInt8, UInt16, UInt32, UInt64,
    Char, String, StrView, Own, Span, readonly, pure,
)
from tpy import (
    Hashable, Representable, Equatable, Comparable,
    ReadOnlySpanLike, NativeIterable, NativeRangeConstructible,
)
from tpy.extern import native, cpp_template, native_preserves_refs
from tpy import error_return


# Python exception hierarchy (maps to ::tpy:: runtime structs in core.hpp)
@native("tpy::BaseException")
class BaseException: ...

@native("tpy::Exception")
class Exception(BaseException): ...

@native("tpy::StopIteration")
class StopIteration(Exception): ...


@native("std::vector")
class list[T](Iterable[T], NativeIterable[T], NativeRangeConstructible[T], ReadOnlySpanLike[T]):
    @overload
    @pure
    @readonly
    @cpp_template("::tpy::collect<std::vector<{T}>>({0})")
    def __init__(self, x: Iterator[T]) -> None: ...

    @overload
    @pure
    @readonly
    @cpp_template("::tpy::from_range<std::vector<{T}>>({0})")
    def __init__(self, x: Iterable[Own[T]]) -> None: ...

    @native("tpy::__iter__", function=True)
    @pure
    @readonly
    def __iter__(self) -> Iterator[T]: ...

    @native("tpy::__len__", function=True)
    @pure
    @readonly
    def __len__(self) -> Int32: ...

    @native("push_back")
    def append(self, value: Own[T]) -> None: ...

    @overload
    @native("tpy::pop_back", function=True)
    def pop(self) -> T: ...

    @overload
    @native("tpy::list_pop_at", function=True)
    def pop(self, index: Int32) -> T: ...

    @native
    def clear(self) -> None: ...

    @cpp_template("{self}[{0}]")
    @pure
    @readonly
    def unchecked_get(self, index: Int32) -> T: ...

    @native("tpy::__getitem__", function=True)
    @pure
    @readonly
    def __getitem__(self, index: Int32) -> T: ...

    @native("tpy::__setitem__", function=True)
    @native_preserves_refs
    def __setitem__(self, index: Int32, value: Own[T]) -> None: ...

    @native("tpy::__delitem__", function=True)
    def __delitem__(self, index: Int32) -> None: ...

    @native("tpy::list_insert", function=True)
    def insert(self, index: Int32, value: Own[T]) -> None: ...

    @native("tpy::list_remove", function=True)
    def remove[T: Equatable](self, value: T) -> None: ...

    @native("tpy::list_extend", function=True)
    def extend(self, other: Iterable[Own[T]]) -> None: ...

    @native("tpy::list_index", function=True)
    @pure
    @readonly
    def index[T: Equatable](self, value: T) -> Int32: ...

    @native("tpy::list_count", function=True)
    @pure
    @readonly
    def count[T: Equatable](self, value: T) -> Int32: ...

    @native("tpy::list_reverse", function=True)
    def reverse(self) -> None: ...

    @cpp_template("std::stable_sort({self}.begin(), {self}.end())")
    def sort[T: Comparable](self) -> None: ...

    @native("tpy::list_copy", function=True)
    @pure
    @readonly
    def copy(self) -> Own[list[T]]: ...

    @native("tpy::list_concat", function=True)
    @pure
    @readonly
    def __add__(self, other: list[T]) -> list[T]: ...

    @native("tpy::list_extend", function=True)
    def __iadd__(self, other: Iterable[Own[T]]) -> list[T]: ...

    @native("tpy::as_span", function=True)
    @pure
    @readonly
    def __span__(self) -> Span[readonly[T]]: ...


@native("tpy::dict_keys_view")
class dict_keys[K, V](Iterable[K], NativeIterable[K]):
    @native("tpy::__len__", function=True)
    @pure
    @readonly
    def __len__(self) -> Int32: ...

    @native("tpy::__iter__", function=True)
    @pure
    @readonly
    def __iter__(self) -> Iterator[K]: ...

    @native("contains")
    @pure
    @readonly
    def __contains__(self, key: K) -> bool: ...


@native("tpy::dict_values_view")
class dict_values[K, V](Iterable[V], NativeIterable[V]):
    @native("tpy::__len__", function=True)
    @pure
    @readonly
    def __len__(self) -> Int32: ...

    @native("tpy::__iter__", function=True)
    @pure
    @readonly
    def __iter__(self) -> Iterator[V]: ...

    @native("contains")
    @pure
    @readonly
    def __contains__(self, value: V) -> bool: ...


@native("tpy::dict_items_view")
class dict_items[K, V](Iterable[tuple[K, V]], NativeIterable[tuple[K, V]]):
    @native("tpy::__len__", function=True)
    @pure
    @readonly
    def __len__(self) -> Int32: ...

    @native("tpy::__iter__", function=True)
    @pure
    @readonly
    def __iter__(self) -> Iterator[tuple[K, V]]: ...

    @native("contains")
    @pure
    @readonly
    def __contains__(self, item: tuple[K, V]) -> bool: ...


@native("tpy::ordered_map")
class dict[K, V](Iterable[K], NativeIterable[K]):
    @overload
    @pure
    @readonly
    @cpp_template("::tpy::dict_collect_pairs<{K}, {V}>({0})")
    def __init__(self, x: Iterator[tuple[K, V]]) -> None: ...

    @overload
    @pure
    @readonly
    @cpp_template("::tpy::dict_from_pairs<{K}, {V}>({0})")
    def __init__(self, x: Iterable[Own[tuple[K, V]]]) -> None: ...

    @native("tpy::__iter__", function=True)
    @pure
    @readonly
    def __iter__(self) -> Iterator[K]: ...

    @native("tpy::__len__", function=True)
    @pure
    @readonly
    def __len__(self) -> Int32: ...

    @native("tpy::__getitem__", function=True)
    @pure
    @readonly
    def __getitem__(self, key: K) -> V: ...

    @native("tpy::__setitem__", function=True)
    @native_preserves_refs
    def __setitem__(self, key: K, value: Own[V]) -> None: ...

    @native("tpy::__delitem__", function=True)
    def __delitem__(self, key: K) -> None: ...

    @native("contains")
    @pure
    @readonly
    def __contains__(self, key: K) -> bool: ...

    @overload
    @native("tpy::dict_get", function=True)
    @pure
    @readonly
    def get(self, key: K) -> V | None: ...

    @overload
    @native("tpy::dict_get_default", function=True)
    @pure
    @readonly
    def get(self, key: K, default: V) -> Own[V]: ...

    @overload
    @native("tpy::dict_pop", function=True)
    def pop(self, key: K) -> Own[V]: ...

    @overload
    @native("tpy::dict_pop_default", function=True)
    def pop(self, key: K, default: V) -> Own[V]: ...

    @native
    def clear(self) -> None: ...

    @native("tpy::dict_update", function=True)
    def update(self, other: dict[K, Own[V]]) -> None: ...

    @native("tpy::dict_update", function=True)
    def __ior__(self, other: dict[K, Own[V]]) -> dict[K, V]: ...

    @native("tpy::dict_setdefault", function=True)
    def setdefault(self, key: K, default: Own[V]) -> Own[V]: ...

    @native("tpy::dict_keys", function=True)
    @pure
    @readonly
    def keys(self) -> dict_keys[K, V]: ...

    @native("tpy::dict_values", function=True)
    @pure
    @readonly
    def values(self) -> dict_values[K, V]: ...

    @native("tpy::dict_items", function=True)
    @pure
    @readonly
    def items(self) -> dict_items[K, V]: ...


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

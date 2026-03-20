# tpy: native_module
# tpy: cpp_namespace("tpystd::tpy")
from typing import overload, Protocol, Iterator, Iterable, Self
from tpy import UInt64, Int32, Own, Span, Ptr, readonly, pure
from tpy.extern import native, cpp_template, builtin_type


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
    def __hash__(self) -> UInt64: ...


class Comparable(Protocol):
    @readonly
    def __lt__(self, other: Self) -> bool: ...


class Equatable(Protocol):
    @readonly
    def __eq__(self, other: Self) -> bool: ...


class Deref[T](Protocol):
    def __deref__(self) -> T: ...


class ReadOnlySpanLike[T](Protocol):
    @readonly
    def __span__(self) -> Span[readonly[T]]: ...


# --- Marker protocols (no methods, map to runtime C++ concepts) ---

@native("tpy::NativeIterable")
class NativeIterable[T](Protocol): ...

@native("tpy::NativeRangeConstructible")
class NativeRangeConstructible[T](Protocol): ...

@native("tpy::ValueType")
class ValueType(Protocol): ...

@native("tpy::Send")
class Send(Protocol): ...

@native("tpy::Sync")
class Sync(Protocol): ...

@native("std::default_initializable")
class Default(Protocol): ...

@native("tpy::Covariant")
class Covariant[T](Protocol): ...

# Fixed-width integer constraints (for generic functions over Int8..UInt64)
@native("tpy::AnyFixedInt")
class AnyFixedInt(Protocol): ...

@native("tpy::AnyFixedSigned")
class AnyFixedSigned(Protocol): ...

@native("tpy::AnyFixedUnsigned")
class AnyFixedUnsigned(Protocol): ...


# --- Types ---

@builtin_type("tpy.Span")
@native("std::span")
class Span[T](Iterable[T], NativeIterable[T], ReadOnlySpanLike[T]):
    @overload
    @cpp_template("{cpp}({0}, static_cast<size_t>({1}))")
    def __init__(self, ptr: Ptr[T], length: Int32) -> None: ...

    @overload
    @cpp_template("{cpp}({0})")
    def __init__(self, source: Span[T]) -> None: ...

    @native("tpy::__iter__", function=True)
    @pure
    @readonly
    def __iter__(self) -> Iterator[T]: ...

    @native("tpy::__len__", function=True)
    @pure
    @readonly
    def __len__(self) -> Int32: ...

    @cpp_template("{self}[{0}]")
    @pure
    @readonly
    def unchecked_get(self, index: Int32) -> T: ...

    @native("tpy::__getitem__", function=True)
    @pure
    @readonly
    def __getitem__(self, index: Int32) -> T: ...

    @native("tpy::__setitem__", function=True)
    def __setitem__(self, index: Int32, value: Own[T]) -> None: ...

    @native("tpy::as_span", function=True)
    @pure
    @readonly
    def __span__(self) -> Span[readonly[T]]: ...

    @cpp_template("std::stable_sort({self}.begin(), {self}.end())")
    def sort[T: Comparable](self) -> None: ...


@builtin_type("tpy.Array")
@native("std::array")
class Array[T, N: int](Iterable[T], NativeIterable[T], ReadOnlySpanLike[T]):
    @native("tpy::__iter__", function=True)
    @pure
    @readonly
    def __iter__(self) -> Iterator[T]: ...

    @native("tpy::__len__", function=True)
    @pure
    @readonly
    def __len__(self) -> Int32: ...

    @cpp_template("{self}[{0}]")
    @pure
    @readonly
    def unchecked_get(self, index: Int32) -> T: ...

    @native("tpy::__getitem__", function=True)
    @pure
    @readonly
    def __getitem__(self, index: Int32) -> T: ...

    @native("tpy::__setitem__", function=True)
    def __setitem__(self, index: Int32, value: Own[T]) -> None: ...

    @native("tpy::as_span", function=True)
    @pure
    @readonly
    def __span__(self) -> Span[readonly[T]]: ...


# --- Functions ---

@pure
@readonly
@native("tpy::as_span")
def span[T](x: ReadOnlySpanLike[T]) -> Span[readonly[T]]: ...

@pure
@readonly
@native("tpy::deref_check")
def deref[T](x: Deref[T]) -> T: ...

@cpp_template("{T}{{}}")
def make_default[T: Default]() -> Own[T]: ...

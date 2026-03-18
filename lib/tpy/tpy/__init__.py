# tpy: native_module
# tpy: cpp_namespace("tpystd::tpy")
from typing import Protocol, Self
from tpy import UInt64, Span, readonly
from tpy.extern import native


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

@native("::tpy::NativeIterable")
class NativeIterable[T](Protocol): ...

@native("::tpy::NativeRangeConstructible")
class NativeRangeConstructible[T](Protocol): ...

@native("::tpy::ValueType")
class ValueType(Protocol): ...

@native("::tpy::Send")
class Send(Protocol): ...

@native("::tpy::Sync")
class Sync(Protocol): ...

@native("std::default_initializable")
class Default(Protocol): ...

@native("::tpy::Covariant")
class Covariant[T](Protocol): ...

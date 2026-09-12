# tpy: native_module
# tpy: cpp_namespace("tpystd::builtins")
from .._typing import Sized, Iterator, Iterable
from .._bootstrap._decorators import readonly, pure, error_return, Own, dispatch
from .._core._types import (
    int8, int16, int32, int64, uint8, uint16, uint32, uint64,
    char, String, StrView, float32, AnyFixedInt,
    Hashable, Representable, Stringable, NativeIterable, Truthy, Comparable, Equatable,
)
from .._bootstrap._extern import native, cpp_template, builtin_type

# Range type and range() function
@builtin_type("builtins.Range")
@native("tpy::Range")
class Range[T](NativeIterable[T], Iterable[T]):
    @native("tpy::__iter__", function=True)
    @readonly
    @pure
    def __iter__(self) -> Iterator[T]: ...


@dispatch
@cpp_template("::tpy::Range<{T}>({0})")
@readonly
@pure
def range[T: AnyFixedInt](stop: T) -> Range[T]: ...
@dispatch
@cpp_template("::tpy::Range<{T}>({0}, {1})")
@readonly
@pure
def range[T: AnyFixedInt](start: T, stop: T) -> Range[T]: ...
@dispatch
@cpp_template("::tpy::Range<{T}>({0}, {1}, {2})")
@readonly
@pure
def range[T: AnyFixedInt](start: T, stop: T, step: T) -> Range[T]: ...

@dispatch
@cpp_template("::tpy::Range<::tpy::BigInt>({0})")
@readonly
@pure
def range(stop: int) -> Range[int]: ...
@dispatch
@cpp_template("::tpy::Range<::tpy::BigInt>({0}, {1})")
@readonly
@pure
def range(start: int, stop: int) -> Range[int]: ...
@dispatch
@cpp_template("::tpy::Range<::tpy::BigInt>({0}, {1}, {2})")
@readonly
@pure
def range(start: int, stop: int, step: int) -> Range[int]: ...

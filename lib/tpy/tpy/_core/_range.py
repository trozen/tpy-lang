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
